from setuptools import setup, find_packages

setup(
    name="caduceo-relay",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "caduceo-common",
        "fastapi>=0.110",
        "uvicorn>=0.29",
        "websockets>=12.0",
        "pyjwt>=2.8",
        "aiosqlite>=0.19",
    ],
    entry_points={
        "console_scripts": [
            "caduceo-relay=caduceo_relay.server:main",
        ],
    },
    python_requires=">=3.10",
)