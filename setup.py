from setuptools import setup, find_packages

setup(
    name="promptcompile",
    version="0.1.0",
    description="Turn English into executables. Prompt -> Binary. No code.",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=["anthropic>=0.40.0"],
    extras_require={"mcp": ["mcp>=1.0.0"]},
    entry_points={
        "console_scripts": [
            "p2b=prompt2bin.cli:main",
            "prompt2bin=prompt2bin.cli:main",
        ],
    },
)
