import logging
import shutil
import zipfile
from pathlib import PurePosixPath
from subprocess import PIPE, CalledProcessError, run as subprocess_run  # nosec
from tempfile import TemporaryFile

from requests.exceptions import ConnectionError as RequestsConnectionError
from rpdk.core.data_loaders import resource_stream
from rpdk.core.exceptions import DownstreamError, SysExitRecommendedError
from rpdk.core.init import input_with_validation
from rpdk.core.jsonutils.resolver import ContainerType, resolve_models
from rpdk.core.plugin_base import LanguagePlugin

from .resolver import translate_type
from .utils import safe_reserved

LOG = logging.getLogger(__name__)

EXECUTABLE = "cfn"
SUPPORT_LIB_NAME = "cfn-rpdk"
MAIN_HANDLER_FUNCTION = "TypeFunction"


class StandardDistNotFoundError(SysExitRecommendedError):
    pass


def validate_no(value):
    return value.lower() not in ("n", "no")


class TypescriptLanguagePlugin(LanguagePlugin):
    MODULE_NAME = __name__
    NAME = "typescript"
    RUNTIME = "nodejs12.x"
    ENTRY_POINT = "dist/handlers.entrypoint"
    TEST_ENTRY_POINT = "dist/handlers.testEntrypoint"
    CODE_URI = "./"

    def __init__(self):
        self.env = self._setup_jinja_env(
            trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True
        )
        self.env.filters["translate_type"] = translate_type
        self.env.filters["safe_reserved"] = safe_reserved
        self.env.globals["ContainerType"] = ContainerType
        self.namespace = None
        self.package_name = None
        self.package_root = None
        self._use_docker = True
        self._build_command = None

    def _init_from_project(self, project):
        self.namespace = tuple(s.lower() for s in project.type_info)
        self.package_name = "-".join(self.namespace)
        self._use_docker = project.settings.get("useDocker", True)
        self.package_root = project.root / "src"
        self._build_command = project.settings.get("buildCommand", None)

    def _prompt_for_use_docker(self, project):
        self._use_docker = input_with_validation(
            "Use docker for platform-independent packaging (Y/n)?\n",
            validate_no,
            "This is highly recommended unless you are experienced \n"
            "with cross-platform Typescript packaging.",
        )
        project.settings["useDocker"] = self._use_docker

    def init(self, project):
        LOG.debug("Init started")

        self._init_from_project(project)
        self._prompt_for_use_docker(project)

        project.runtime = self.RUNTIME
        project.entrypoint = self.ENTRY_POINT
        project.test_entrypoint = self.TEST_ENTRY_POINT

        def _render_template(path, **kwargs):
            LOG.debug("Writing '%s'", path)
            template = self.env.get_template(path.name)
            contents = template.render(**kwargs)
            project.safewrite(path, contents)

        def _copy_resource(path, resource_name=None):
            LOG.debug("Writing '%s'", path)
            if not resource_name:
                resource_name = path.name
            contents = resource_stream(__name__, f"data/{resource_name}").read()
            project.safewrite(path, contents)

        # handler Typescript package
        handler_package_path = self.package_root
        LOG.debug("Making folder '%s'", handler_package_path)
        handler_package_path.mkdir(parents=True, exist_ok=True)
        _render_template(
            handler_package_path / "handlers.ts",
            lib_name=SUPPORT_LIB_NAME,
            type_name=project.type_name,
        )
        # models.ts produced by generate

        # project support files
        _copy_resource(project.root / ".gitignore", "typescript.gitignore")
        _copy_resource(project.root / ".npmrc", ".npmrc")
        _copy_resource(project.root / "tsconfig.json", "tsconfig.json")
        _render_template(
            project.root / "package.json",
            name=project.hypenated_name,
            description="AWS custom resource provider named {}.".format(project.type_name),
            lib_name=SUPPORT_LIB_NAME,
        )
        _render_template(
            project.root / "README.md",
            type_name=project.type_name,
            schema_path=project.schema_path,
            project_path=self.package_name,
            executable=EXECUTABLE,
            lib_name=SUPPORT_LIB_NAME,
        )

        # CloudFormation/SAM template for handler lambda
        handler_params = {
            "Handler": project.entrypoint,
            "Runtime": project.runtime,
            "CodeUri": self.CODE_URI,
        }
        handler_function = {
            "TestEntrypoint": {
                **handler_params,
                "Handler": project.test_entrypoint,
            },
        }
        handler_function[MAIN_HANDLER_FUNCTION] = handler_params
        _render_template(
            project.root / "template.yml",
            resource_type=project.type_name,
            functions=handler_function,
        )

        LOG.debug("Init complete")

    def generate(self, project):
        LOG.debug("Generate started")

        self._init_from_project(project)

        models = resolve_models(project.schema)

        path = self.package_root / "models.ts"
        LOG.debug("Writing file: %s", path)
        template = self.env.get_template("models.ts")
        contents = template.render(
            lib_name=SUPPORT_LIB_NAME,
            type_name=project.type_name,
            models=models,
        )
        project.overwrite(path, contents)

        LOG.debug("Generate complete")

    def _pre_package(self, build_path):
        f = TemporaryFile("w+b")

        with zipfile.ZipFile(f, mode="w") as zip_file:
            self._recursive_relative_write(build_path, build_path, zip_file)
        f.seek(0)

        return f

    @staticmethod
    def _recursive_relative_write(src_path, base_path, zip_file):
        for path in src_path.rglob("*"):
            if path.is_file():
                relative = path.relative_to(base_path)
                zip_file.write(path.resolve(), str(relative))

    def package(self, project, zip_file):
        LOG.debug("Package started")

        self._init_from_project(project)

        handler_package_path = self.package_root
        build_path = project.root / "build"

        self._remove_build_artifacts(build_path)
        self._build(project.root)

        inner_zip = self._pre_package(build_path / MAIN_HANDLER_FUNCTION)
        zip_file.writestr("ResourceProvider.zip", inner_zip.read())
        self._recursive_relative_write(handler_package_path, project.root, zip_file)

        LOG.debug("Package complete")

    @staticmethod
    def _remove_build_artifacts(deps_path):
        try:
            shutil.rmtree(deps_path)
        except FileNotFoundError:
            LOG.debug("'%s' not found, skipping removal", deps_path, exc_info=True)

    @staticmethod
    def _make_build_command(base_path, build_command=None):
        command = f"sam build --build-dir {base_path}/build"
        if build_command:
            command = build_command
        return command

    def _build(self, base_path):
        LOG.debug("Dependencies build started from '%s'", base_path)

        # TODO: We should use the build logic from SAM CLI library, instead:
        # https://github.com/awslabs/aws-sam-cli/blob/master/samcli/lib/build/app_builder.py
        command = self._make_build_command(base_path, self._build_command)
        if self._use_docker:
            command = command + " --use-container"
        command = command + " " + MAIN_HANDLER_FUNCTION

        LOG.debug("command is '%s'", command)

        LOG.warning("Starting build.")
        try:
            completed_proc = subprocess_run(  # nosec
                ["/bin/bash", "-c", command], stdout=PIPE, stderr=PIPE, cwd=base_path, check=True
            )
        except (FileNotFoundError, CalledProcessError) as e:
            raise DownstreamError("local build failed") from e

        LOG.debug("--- build stdout:\n%s", completed_proc.stdout)
        LOG.debug("--- build stderr:\n%s", completed_proc.stderr)
        LOG.debug("Dependencies build finished")