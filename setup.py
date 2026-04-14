from setuptools import find_packages, setup

install_requires = []
with open("requirements.txt") as f:
	for line in f:
		line = line.strip()
		if line and not line.startswith("#"):
			install_requires.append(line)

setup(
    name="call_intelligence",
    version="0.1.0",
    description="Patient communication and qualification tools for Frappe CRM",
    author="Anjali",
    license="MIT",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
)
