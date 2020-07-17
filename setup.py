"""Setup script for the Flockwave GPS package."""

from setuptools import setup, find_packages

requires = [
    "anyio>=1.2.1",
    "async-exit-stack>=1.0.1",
    "async-generator>=1.10",
    "colorama>=0.4.3",
    "hexdump>=3.3",
    "outcome>=1.0.1",
    "pyusb>=1.0.2"
]

__version__ = None
exec(open("src/aiocflib/version.py").read())

setup(
    name="aiocflib",
    version=__version__,
    author=u"Tam\u00e1s Nepusz",
    author_email="tamas@collmot.com",
    packages=find_packages(exclude=["test"]),
    include_package_data=True,
    install_requires=requires,
    test_suite="test",
)
