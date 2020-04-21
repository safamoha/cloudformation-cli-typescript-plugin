#!/usr/bin/env python
"""Typescript language support for the CloudFormation CLI"""
import os.path
import re

from setuptools import setup

HERE = os.path.abspath(os.path.dirname(__file__))


def read(*parts):
    with open(os.path.join(HERE, *parts), "r", encoding="utf-8") as fp:
        return fp.read()


# https://packaging.python.org/guides/single-sourcing-package-version/
def find_version(*file_paths):
    version_file = read(*file_paths)
    version_match = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]", version_file, re.M)
    if version_match:
        return version_match.group(1)
    raise RuntimeError("Unable to find version string.")


setup(
    name="cloudformation-cli-typescript-plugin",
    version=find_version("python", "rpdk", "typescript", "__init__.py"),
    description=__doc__,
    long_description=read("README.md"),
    author="eduardomourar",
    url="https://github.com/eduardomourar/cloudformation-cli-typescript-plugin",
    packages=["rpdk.typescript"],
    package_dir={"": "python"},
    # package_data -> use MANIFEST.in instead
    include_package_data=True,
    zip_safe=True,
    python_requires=">=3.6",
    install_requires=["cloudformation-cli>=0.1,<0.2", "docker>=3.7,<3.8"],
    entry_points={
        "rpdk.v1.languages": [
            "typescript = rpdk.typescript.codegen:TypescriptLanguagePlugin",
        ]
    },
    license="MIT",
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT/X Consortium License",
        "Natural Language :: English",
        "Topic :: Software Development :: Build Tools",
        "Topic :: Software Development :: Code Generators",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
    ],
    keywords="Amazon Web Services AWS CloudFormation",
)