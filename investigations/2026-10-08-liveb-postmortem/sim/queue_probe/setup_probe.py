import os

import nautilus_trader
from Cython.Build import cythonize
from setuptools import Extension, setup

site = os.path.dirname(os.path.dirname(nautilus_trader.__file__))
nt = os.path.dirname(nautilus_trader.__file__)
ext = Extension("nt_queue_probe", ["nt_queue_probe.pyx"],
                include_dirs=[site, os.path.join(nt, "core", "includes"), os.path.join(nt, "core", "rust")],
                extra_compile_args=["-Wno-unreachable-code", "-Wno-unused-function"])
setup(
    ext_modules=cythonize([ext], include_path=[site], language_level=3, quiet=True),
    script_args=["build_ext", "--inplace"],
)
