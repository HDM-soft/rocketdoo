from setuptools import setup, find_packages

setup(
    name='rocketdoo',
    version='1.0',
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
