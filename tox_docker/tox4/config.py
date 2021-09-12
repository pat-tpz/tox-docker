from collections import defaultdict
from typing import (
    Any,
    Callable,
    cast,
    Container,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
)
import re

from docker.types import Mount
from tox.config.main import Config
from tox.config.set_env import SetEnv
from tox.config.sets import ConfigSet
from tox.tox_env.api import ToxEnv
import py

from tox_docker.config import (
    ContainerConfig,
    RunningContainers,
    validate_link,
    validate_port,
    validate_volume,
)
from tox_docker.tox4.log import log

# nanoseconds in a second; named "SECOND" so that "1.5 * SECOND" makes sense
SECOND = 1000000000

EnvRunningContainers = Dict[ToxEnv, RunningContainers]


class MissingRequiredSetting(Exception):
    pass


def required(setting_name: str) -> Callable[[str], str]:
    def require_value(val: str) -> str:
        if not val:
            raise MissingRequiredSetting(setting_name)
        return val

    return require_value


class EnvDockerConfigSet(ConfigSet):
    def __init__(self, conf: Config) -> None:
        super().__init__(conf)

        self.add_config(
            keys=["docker"],
            of_type=List[str],
            default=[],
            desc="docker image configs to load",
        )

    @property
    def name(self) -> str:
        return ""


class DockerConfigSet(ConfigSet):
    def __init__(self, conf: Config) -> None:
        super().__init__(conf)

        self.add_config(
            keys=["image"],
            of_type=str,
            default="",
            post_process=required("image"),
            desc="docker image to run",
        )
        self.add_config(
            keys=["environment"],
            of_type=SetEnv,
            default=SetEnv(""),
            desc="environment variables to pass to the docker container",
        )
        self.add_config(
            keys=["ports"],
            of_type=List[str],
            default=[],
            desc="ports to expose",
            post_process=list,
        )
        self.add_config(
            keys=["links"],
            of_type=List[str],
            default=[],
            desc="containers to link",
            post_process=list,
        )
        self.add_config(
            keys=["volumes"],
            of_type=List[str],
            default=[],
            desc="volumes to attach",
            post_process=list,
        )

        self.add_config(
            keys=["healthcheck_cmd"],
            of_type=str,
            default="",
            desc="docker healthcheck command",
        )
        self.add_config(
            keys=[
                "healthcheck_interval",
                "healthcheck_timeout",
                "healthcheck_start_period",
                "healthcheck_retries",
            ],
            of_type=float,
            default=0,
            desc="docker healthcheck parameters",
            post_process=lambda num: int(num * SECOND),
        )

    @property
    def name(self) -> str:
        return ""


def discover_container_configs(config: Config) -> Sequence[str]:
    """
    Read the tox.ini, and return a list of docker container configs.

    """

    docker_configs = set()
    for env_name in config:
        env_config = config.get_section_config(
            f"testenv:{env_name}", EnvDockerConfigSet
        )
        docker_configs.update(env_config.load("docker"))

    return list(docker_configs)


def parse_container_config(
    config: Config, container_name: str, all_container_names: Container[str]
) -> ContainerConfig:
    section = config.get_section_config(f"docker:{container_name}", DockerConfigSet)

    kwargs = {
        "name": container_name,
        "image": section["image"],
        "stop": container_name not in config.options.docker_dont_stop,
    }

    if section["environment"]:
        kwargs["environment"] = {
            k: section["environment"].load(k) for k in section["environment"]
        }

    if section["healthcheck_cmd"]:
        kwargs["healthcheck_cmd"] = section["healthcheck_cmd"]
    if section["healthcheck_interval"]:
        kwargs["healthcheck_interval"] = section["healthcheck_interval"]
    if section["healthcheck_timeout"]:
        kwargs["healthcheck_timeout"] = section["healthcheck_timeout"]
    if section["healthcheck_start_period"]:
        kwargs["healthcheck_start_period"] = section["healthcheck_start_period"]
    if section["healthcheck_retries"]:
        kwargs["healthcheck_retries"] = section["healthcheck_retries"]

    if section["ports"]:
        ports = defaultdict(set)
        for port_mapping in section["ports"]:
            host_port, container_port_proto = validate_port(port_mapping)
            ports[container_port_proto].add(host_port)

        kwargs["ports"] = {k: list(v) for k, v in ports.items()}

    if section["links"]:
        kwargs["links"] = dict(
            validate_link(link_line, all_container_names)
            for link_line in section["links"]
            if link_line.strip()
        )

    if section["volumes"]:
        kwargs["mounts"] = [
            validate_volume(volume_line)
            for volume_line in section["volumes"]
            if volume_line.strip()
        ]

    return ContainerConfig(**kwargs)