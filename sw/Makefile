# SPDX-License-Identifier: MIT
# openISP firmware: libopenisp + the openisp tool / 3A daemon, oisp_view (HDMI/DP preview).
#   make                                   native build (host tests, or on the KV260)
#   make CROSS_COMPILE=aarch64-linux-gnu-  cross build for the KV260 (Kria Ubuntu, glibc)
#   make install DESTDIR=... PREFIX=/usr/local
CC      = $(CROSS_COMPILE)gcc
AR      = $(CROSS_COMPILE)ar
CFLAGS ?= -O2 -g
override CFLAGS += -std=gnu99 -Wall -Wextra -Wno-unused-parameter -Iinclude -Ilib
LDLIBS  = -lm
PREFIX ?= /usr/local
BUILD  ?= build

LIBSRC = lib/oisp_cfg.c lib/oisp_json.c lib/oisp_aaa.c lib/oisp_tuning.c lib/oisp_hal.c lib/oisp_sensor.c
LIBOBJ = $(LIBSRC:%.c=$(BUILD)/%.o)

all: $(BUILD)/openisp $(BUILD)/oisp_view

$(BUILD)/%.o: %.c include/openisp_regs.h include/openisp_uapi.h lib/oisp.h
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c -o $@ $<

$(BUILD)/libopenisp.a: $(LIBOBJ)
	$(AR) rcs $@ $^

$(BUILD)/openisp: $(BUILD)/app/openisp.o $(BUILD)/libopenisp.a
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

$(BUILD)/oisp_view: $(BUILD)/app/oisp_view.o
	$(CC) $(LDFLAGS) -o $@ $^

install: all
	install -D -m 755 $(BUILD)/openisp $(DESTDIR)$(PREFIX)/bin/openisp
	install -D -m 755 $(BUILD)/oisp_view $(DESTDIR)$(PREFIX)/bin/oisp_view
	install -D -m 644 systemd/openisp-3a.service $(DESTDIR)/etc/systemd/system/openisp-3a.service

clean:
	rm -rf $(BUILD)

.PHONY: all install clean
