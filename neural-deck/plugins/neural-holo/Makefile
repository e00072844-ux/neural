CXXFLAGS ?= -O2 -g
FLAGS = -std=c++17 -fPIC -shared -DWLR_USE_UNSTABLE -DWAYFIRE_PLUGIN $(shell pkg-config --cflags wayfire wf-config wlroots-0.20 glesv2)
LIBS = $(shell pkg-config --libs wayfire wf-config glesv2)

libneural-holo.so: neural-holo.cpp
	$(CXX) $(CXXFLAGS) $(FLAGS) -o $@ $< $(LIBS)

install: libneural-holo.so
	install -Dm755 libneural-holo.so $(DESTDIR)$(shell pkg-config --variable=plugindir wayfire)/libneural-holo.so
	install -Dm644 neural-holo.xml $(DESTDIR)$(shell pkg-config --variable=metadatadir wayfire)/neural-holo.xml
