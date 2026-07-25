from setuptools import find_packages, setup

setup(
    name="apple-health-insights",
    version="1.0.0",
    description="Analyse Apple Health exports without loading gigabytes into memory",
    author="Sydney Gahunia",
    packages=find_packages(exclude=["tests", "tests.*"]),
    python_requires=">=3.10",
    extras_require={"charts": ["matplotlib>=3.6"]},
    entry_points={"console_scripts": ["health-insights=healthinsights.cli:main"]},
)
