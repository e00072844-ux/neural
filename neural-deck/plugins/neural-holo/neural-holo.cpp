// NEURAL//HOLO — efecto de apertura/cierre de ventanas tipo holograma para Wayfire 0.11.
//
// Se registra en el registro de efectos del plugin animate como "holograma":
//   [animate] open_animation = holograma / close_animation = holograma
//
// Al abrir: aparece el marco de la ventana y el contenido se "proyecta" de abajo
// hacia arriba con un haz de luz en el frente, líneas de barrido, desfase de
// color y bandas con glitch que se estabilizan al terminar.
// Al cerrar: lo mismo al revés (se deshace de arriba hacia abajo).

#include <wayfire/plugin.hpp>
#include <wayfire/core.hpp>
#include <wayfire/config/config-manager.hpp>
#include <wayfire/util.hpp>
#include <wayfire/view.hpp>
#include <wayfire/view-transform.hpp>
#include <wayfire/opengl.hpp>
#include <wayfire/render.hpp>
#include <wayfire/scene-render.hpp>
#include <wayfire/option-wrapper.hpp>
#include <wayfire/config/types.hpp>
#include <wayfire/util/duration.hpp>
#include <wayfire/plugins/common/shared-core-data.hpp>
#include <wayfire/plugins/animate/animate.hpp>

#include <memory>
#include <string>

namespace
{
const char *VERTEX = R"(
#version 100
attribute mediump vec2 position;
attribute mediump vec2 uvPosition;
varying mediump vec2 uvpos;
uniform mat4 mvp;
void main()
{
    gl_Position = mvp * vec4(position, 0.0, 1.0);
    uvpos = uvPosition;
}
)";

// uvpos.y = 0 abajo, 1 arriba (misma convención que render_transformed_texture).
const char *FRAGMENT = R"(
#version 100
@builtin_ext@
precision highp float;
@builtin@
varying mediump vec2 uvpos;
uniform float progress;   // 0 = nada, 1 = ventana completa
uniform float tiempo;     // segundos
uniform vec3 color;       // color del holograma (acento del tema)
uniform vec2 size;        // tamaño lógico en px

float hash(vec2 p)
{
    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
}

vec4 muestra(vec2 uv)
{
    if (uv.x < 0.0 || uv.x > 1.0) { return vec4(0.0); }
    return get_pixel(uv);
}

void main()
{
    float p = clamp(progress, 0.0, 1.0);
    float k = 1.0 - p;                         // inestabilidad
    float py = uvpos.y * size.y;               // px desde abajo
    float px = uvpos.x * size.x;
    const float BORDE = 28.0;
    float frente = p * (size.y + 2.0 * BORDE) - BORDE;
    float d = frente - py;                     // > 0 = ya proyectado
    float mascara = clamp(d / 12.0, 0.0, 1.0);
    float cerca = exp(-max(d, 0.0) / 90.0);    // 1 junto al frente

    // glitch por bandas horizontales
    float t = floor(tiempo * 24.0);
    float banda = floor(py / 7.0);
    float off = 0.0;
    if (hash(vec2(banda, t)) > 0.86) {
        off = (hash(vec2(t, banda + 3.0)) - 0.5) * 40.0 * k;
    }
    off += (hash(vec2(banda * 1.7, t)) - 0.5) * 12.0 * cerca * k;
    vec2 uv = uvpos + vec2(off / size.x, 0.0);

    // desfase cromático
    float ca = (1.5 + 5.0 * cerca) * k / size.x;
    vec4 c  = muestra(uv);
    vec4 cr = muestra(uv + vec2(ca, 0.0));
    vec4 cb = muestra(uv - vec2(ca, 0.0));
    vec4 col = vec4(cr.r, c.g, cb.b, max(c.a, max(cr.a, cb.a)));

    // tinte holográfico + líneas de barrido + parpadeo
    float lum = dot(col.rgb, vec3(0.299, 0.587, 0.114));
    vec3 holo = color * (0.30 + 0.95 * lum) * col.a;
    vec3 rgb = mix(col.rgb, holo, clamp(k * (0.45 + 0.5 * cerca), 0.0, 1.0));
    rgb *= 1.0 - 0.38 * k * step(0.5, fract(py / 3.0));
    float parpadeo = 1.0 - 0.28 * k * hash(vec2(t, 7.0));
    vec4 salida = vec4(rgb, col.a) * mascara * parpadeo * (0.55 + 0.45 * p);

    // marco del proyector (aparece antes que el contenido)
    float dborde = min(min(px, size.x - px), min(py, size.y - py));
    float marco = 1.0 - smoothstep(0.0, 1.6, dborde);
    marco *= 0.75 * smoothstep(0.0, 0.15, k) * (0.6 + 0.4 * hash(vec2(t, 11.0)));
    salida.rgb += color * marco;
    salida.a = max(salida.a, marco * 0.8);

    // haz de luz en el frente
    float haz = exp(-abs(d) / 2.5) + 0.35 * exp(-abs(d) / 22.0);
    haz *= smoothstep(0.0, 0.08, k);
    salida.rgb += color * haz;
    salida.a = clamp(salida.a + haz * 0.35, 0.0, 1.0);

    gl_FragColor = salida;
}
)";

std::unique_ptr<OpenGL::program_t> programa;
}

namespace neural
{
class holo_node_t;

class holo_render_instance_t : public wf::scene::transformer_render_instance_t<holo_node_t>
{
  public:
    using transformer_render_instance_t::transformer_render_instance_t;
    void render(const wf::scene::render_instruction_t& data) override;
};

class holo_node_t : public wf::scene::transformer_base_node_t
{
  public:
    float progress = 0.0f;
    wf::color_t color{0.0, 0.9, 1.0, 1.0};

    holo_node_t() : transformer_base_node_t(false)
    {}

    std::string stringify() const override
    {
        return "neural-holo";
    }

    void gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
        wf::scene::damage_callback push_damage, wf::output_t *shown_on) override
    {
        auto inst = std::make_unique<holo_render_instance_t>(this, push_damage, shown_on);
        if (inst->has_instances())
        {
            instances.push_back(std::move(inst));
        }
    }
};

void holo_render_instance_t::render(const wf::scene::render_instruction_t& data)
{
    const auto bbox = self->get_children_bounding_box();
    auto tex = get_texture(data.target.scale);
    if (!tex)
    {
        return;
    }

    // Seguridad: sin shader válido (o sin GLES) → fundido normal, nunca tumbar Wayfire.
    const auto fundido = [&]
    {
        data.pass->add_texture(tex, data.target, bbox, data.damage, self->progress);
    };
    if (!programa)
    {
        return fundido();
    }

    bool hecho = false;
    data.pass->custom_gles_subpass([&]
    {
        wf::gles_texture_t gtex{tex};
        if (programa->get_program_id(gtex.type) == 0)
        {
            return;
        }

        hecho = true;
        programa->use(gtex.type);
        programa->set_active_texture(gtex);

        const float x1 = bbox.x, y1 = bbox.y;
        const float x2 = bbox.x + bbox.width, y2 = bbox.y + bbox.height;
        const GLfloat vertices[] = {x1, y2, x2, y2, x2, y1, x1, y1};
        const GLfloat coords[]   = {0, 0, 1, 0, 1, 1, 0, 1};
        programa->attrib_pointer("position", 2, 0, vertices);
        programa->attrib_pointer("uvPosition", 2, 0, coords);
        programa->uniformMatrix4f("mvp", wf::gles::render_target_orthographic_projection(data.target));
        programa->uniform1f("progress", self->progress);
        programa->uniform1f("tiempo", (wf::get_current_time() % 1000000) / 1000.0f);
        programa->uniform3f("color", self->color.r, self->color.g, self->color.b);
        programa->uniform2f("size", bbox.width, bbox.height);

        wf::gles::for_each_scissor_rect(data.target, data.damage, [&]
        {
            GL_CALL(glDrawArrays(GL_TRIANGLE_FAN, 0, 4));
        });
        programa->deactivate();
    });
    if (!hecho)
    {
        fundido();
    }
}

static const std::string NOMBRE = "neural-holo";

// Opciones leídas con tolerancia: si el XML aún no está cargado (plugin cargado en
// caliente), se usan valores por defecto en vez de lanzar una excepción.
template<class T>
static T opcion(const std::string& nombre, T defecto)
{
    auto raw = wf::get_core().config->get_option(nombre);
    if (auto op = std::dynamic_pointer_cast<wf::config::option_t<T>>(raw))
    {
        return op->get_value();
    }

    return defecto;
}

class holo_animation_t : public wf::animate::animation_base_t
{
    wayfire_view view;
    std::shared_ptr<holo_node_t> node;
    std::unique_ptr<wf::animation::simple_animation_t> anim;

  public:
    void init(wayfire_view view, wf::animation_description_t duracion,
        wf::animate::animation_type type) override
    {
        this->view = view;
        anim = std::make_unique<wf::animation::simple_animation_t>(
            wf::create_option<wf::animation_description_t>(duracion));
        if (type & WF_ANIMATE_HIDING_ANIMATION)
        {
            anim->animate(1, 0);
        } else
        {
            anim->animate(0, 1);
        }

        node = std::make_shared<holo_node_t>();
        node->color    = opcion<wf::color_t>("neural-holo/color", wf::color_t{0.0, 0.9, 1.0, 1.0});
        node->progress = (type & WF_ANIMATE_HIDING_ANIMATION) ? 1.0f : 0.0f;
        view->get_transformed_node()->add_transformer(node, wf::TRANSFORMER_HIGHLEVEL, NOMBRE);
    }

    bool step() override
    {
        auto tm = view->get_transformed_node();
        tm->begin_transform_update();
        node->progress = (double)*anim;
        tm->end_transform_update();
        return anim->running();
    }

    void reverse() override
    {
        anim->reverse();
    }

    ~holo_animation_t() override
    {
        if (view)
        {
            view->get_transformed_node()->rem_transformer<holo_node_t>(NOMBRE);
        }
    }
};

class plugin_t : public wf::plugin_interface_t
{
    wf::shared_data::ref_ptr_t<wf::animate::animate_effects_registry_t> registro;

  public:
    void init() override
    {
        wf::gles::run_in_context_if_gles([&]
        {
            programa = std::make_unique<OpenGL::program_t>();
            programa->compile(VERTEX, FRAGMENT);
        });

        registro->register_effect("holograma", wf::animate::effect_description_t{
            .generator = [] { return std::make_unique<holo_animation_t>(); },
            .default_duration = []
            {
                return opcion<wf::animation_description_t>("neural-holo/duration",
                    wf::animation_description_t{520, wf::animation::smoothing::linear, "linear"});
            },
        });
    }

    void fini() override
    {
        registro->unregister_effect("holograma");
        wf::gles::run_in_context_if_gles([&]
        {
            if (programa)
            {
                programa->free_resources();
            }

            programa.reset();
        });
    }
};
}

DECLARE_WAYFIRE_PLUGIN(neural::plugin_t);
