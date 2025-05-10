from setuptools import setup, find_packages
from pathlib import Path

this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text()

setup(
    name='rocketdoo',
    version='1.2.1',
    description='This library allows you to build an automated local development environment for Odoo EE and CE.',
    long_description=long_description,
    long_description_content_type='text/markdown',
    author='Horacio Montaño and Elias Braceras',
    author_email='horaciomontano@hdmsoft.com.ar',
    url='https://github.com/HDM-soft/rocketdoo.git',
    packages=find_packages(),
    install_requires=[
        'copier',  # Asegúrate de incluir copier en las dependencias
        # Incluye aquí cualquier otra dependencia que tu proyecto necesite
    ],
    entry_points={
        'console_scripts': [
            'rocketdoo=rocketdoo:main',
        ],
    },
)
