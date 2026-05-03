from setuptools import setup, find_packages

setup(
    name="caduceo-agent",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "caduceo-common",
        "websockets>=12.0",
        "psutil>=5.9",
        "pillow>=10.0",
    ],
    extras_require={
        "windows": ["pywin32>=306", "pyautogui>=0.9"],
        "screenshot": ["pyautogui>=0.9"],
    },
    entry_points={
        "console_scripts": [
            "caduceo-agent=caduceo_agent.client:main",
        ],
    },
    python_requires=">=3.10",
)