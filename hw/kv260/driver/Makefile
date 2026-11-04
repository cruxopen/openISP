# SPDX-License-Identifier: GPL-2.0
# Build on the board:  make            (needs linux-headers-$(uname -r))
#            install:  sudo make install
# The register map and the ioctl ABI are shared with the firmware: sw/include
# (copy sw/ next to hw/ on the board, or set OPENISP_INC).
obj-m := openisp.o

KDIR ?= /lib/modules/$(shell uname -r)/build
OPENISP_INC ?= $(abspath $(CURDIR)/../../../sw/include)
ccflags-y += -I$(OPENISP_INC)

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) OPENISP_INC=$(OPENISP_INC) modules

install: all
	install -D -m 644 openisp.ko /lib/modules/$(shell uname -r)/extra/openisp.ko
	depmod -a

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean
