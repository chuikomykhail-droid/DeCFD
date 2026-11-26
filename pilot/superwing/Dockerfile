# Pilot image: the MDO Lab MACH-Aero stack (ADflow, pyHyp, cgnsutilities) plus the
# TU Munich SuperWing scripts. A job pins the digest of the image built from this file.
#
#   docker build -t decfd-superwing pilot/superwing
#   docker run --rm -v <case dir>:/sim/cases/case decfd-superwing --np 6 --conditions 0
#
# The case directory must be writable by uid 1000 (mdolabuser, the image's user).

# u22-gcc-ompi-stable as pulled on 27 Nov 2026: OpenMPI 4.1.6, Python 3.11, numpy 1.25.2
FROM mdolab/public@sha256:5125c855fb3b4979c642d23c58c5e5caa869f5eb703dd9b68a68c6ecc0098c61

# The MDO Lab environment (MPI, pyenv Python, CGNS, PETSc) lives in this file
SHELL ["/bin/bash", "-c"]

# CST geometry: Yang Yunjia's fork of cst-modeling3d (MIT), at the commit that was its head
# while SuperWing was generated (Nov 2024 - Mar 2025). PyPI releases lack
# cst_modeling.io.read_plot3d and cst_modeling.basic.rotation_3d, which gen-mesh imports.
# --no-deps: it needs numpy, scipy and matplotlib, which the image has; letting pip
# resolve them could upgrade numpy under ADflow's compiled modules.
ARG CST_COMMIT=88757e26e6d7809261edd84a2ba1ebb6b2d811aa
RUN source "$BASHRC_MDOLAB" \
 && pip install --no-cache-dir --no-deps \
      "cst-modeling3d @ https://github.com/YangYunjia/cst-modeling3d/archive/${CST_COMMIT}.tar.gz" \
 && python -c "from cst_modeling.section import cst_foil; from cst_modeling.basic import rotation_3d; from cst_modeling.io import read_plot3d, output_plot3d_concat; import adflow, pyhyp, cgnsutilities, numpy; print('imports ok, numpy', numpy.__version__)"

COPY vendor/ /sim/vendor/
COPY vendor/original_tip.xyz /sim/original_tip.xyz
COPY run_case.py /sim/run_case.py

WORKDIR /sim/cases/case
ENTRYPOINT ["bash", "-c", "source \"$BASHRC_MDOLAB\" 2>/dev/null && exec python /sim/run_case.py \"$@\"", "run_case"]
