from setuptools import setup, find_packages

setup(
    name="caduceo-common",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "cryptography>=42.0",
    ],
    python_requires=">=3.10",
)