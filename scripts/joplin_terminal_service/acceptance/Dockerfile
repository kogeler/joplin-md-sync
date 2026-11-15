FROM docker.io/library/python@sha256:a66d3a463d0be3cc537c269b0d3418746dda512417a975559a0eaa8a6406baaf AS python
FROM docker.io/library/node@sha256:c4d5523090a817b7aa86d2111241fdd4f66d1e27782b44160e6aa63b357ecb2d
COPY --from=python /usr/local/ /usr/local/
COPY joplin_terminal_installer/ /installer/joplin_terminal_installer/
ENV JOPLIN_TEST_PREFIX=/opt/joplin-service-test
ENV JOPLIN_TEST_VERSION=3.7.1
RUN PYTHONPATH=/installer python3 -c 'import os, shutil; from pathlib import Path; from joplin_terminal_installer import cli, commands, common, models, releases; a=cli.build_parser({}).parse_args(["--joplin-prefix", os.environ["JOPLIN_TEST_PREFIX"]]); r=commands.CommandRunner(common.SecretRedactor()); d=models.Dependencies(Path(shutil.which("node")), Path(shutil.which("npm")), Path("/usr/bin/systemctl"), "22", "10"); releases.install_or_update_joplin(r,d,cli.build_paths(a),os.environ["JOPLIN_TEST_VERSION"])'
WORKDIR /workspace
CMD ["python3", "scripts/joplin_terminal_service/acceptance/test_s3.py"]
