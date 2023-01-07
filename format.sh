#!/usr/bin/env bash
# find . -name '*.py' -and -not -path './.git/*' -and -not -path './archive/*' | xargs -0 -P $(nproc) yapf -p -i
find . -name '*.py' -and -not -path './.git/*' -and -not -path './archive/*' -and -not -path './xystitch/*' -exec echo {} \; -exec yapf3 -p -i {} \;

