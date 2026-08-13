#!/bin/sh
# /usr/bin/chrome-multi-session-gui -> the bundled front-end.
#
# CMS_CORE_EXE names the core explicitly. The GUI would find it anyway by looking
# beside its own executable (cms_gui.core.frozen_core), but saying it here means
# the .desktop entry, the terminal and any future layout all agree on one answer.
CMS_CORE_EXE=/opt/chrome-multi-session/core/chrome-multi-session-core
export CMS_CORE_EXE

exec /opt/chrome-multi-session/gui/chrome-multi-session-gui "$@"
