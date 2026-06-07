"""Install BayesExpert as an importable package."""
from setuptools import setup, find_packages

with open('README.md', 'r', encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='bayesexpert',
    version='0.1.0',
    description='Literature-derived Bayesian expert system with windowed-polytope CPT solving',
    long_description=long_description,
    long_description_content_type='text/markdown',
    packages=find_packages(include=['sn_bayes', 'sn_bayes.*']),
    python_requires='>=3.10',
    install_requires=[
        'numpy',
        'pandas',
        'openpyxl',
        'scipy',
        'protobuf',
        'pomegranate',
        'qpsolvers',
        'tqdm',
    ],
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Topic :: Scientific/Engineering :: Artificial Intelligence',
    ],
)
