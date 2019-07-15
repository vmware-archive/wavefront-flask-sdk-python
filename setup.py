# coding: utf-8

"""
    Wavefront Flask SDK
    <p>This is a Wavefront Flask SDK</p>  # noqa: E501
"""

from setuptools import setup, find_packages  # noqa: H301

NAME = 'wavefront_flask_sdk'
VERSION = '0.1.0'
# To install the library, run the following
#
# python setup.py install
#
# prerequisite: setuptools
# http://pypi.python.org/pypi/setuptools

REQUIRES = ['opentracing>=2.0,<3', 'six', 'flask', 'wavefront-pyformance', 'Werkzeug']

setup(
    name=NAME,
    version=VERSION,
    description="Wavefront Flask Python SDK",
    author_email="songhao@vmware.com",
    url="https://github.com/wavefrontHQ/wavefront-flask-sdk-python",
    keywords=["Wavefront SDK", "Wavefront", "Flask"],
    install_requires=REQUIRES,
    packages=find_packages(exclude=["*.tests", "*.tests.*", "tests.*", "tests"]),
    include_package_data=True,
    long_description="""\
    """
)
