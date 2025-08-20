from setuptools import setup, find_packages
from pathlib import Path

this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text()

setup(
    name='rocketdoo',
    version='1.3.1',
    description='This library allows you to build an automated local development environment for Odoo EE and CE.',
    long_description=long_description,
    long_description_content_type='text/markdown',
    author='Horacio Montaño and Elias Braceras',
    author_email='horaciomontano@hdmsoft.com.ar',
    url='https://github.com/HDM-soft/rocketdoo.git',
    packages=find_packages(),
    install_requires=[
        'copier',  
    ],
    entry_points={
        'console_scripts': [
            'rocketdoo=rocketdoo:main',
        ],
    },
    license="GPL-3.0-or-later",
    license_files=["LICENSE"]
)
