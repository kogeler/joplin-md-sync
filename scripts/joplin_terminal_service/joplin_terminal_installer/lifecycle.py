"""Joplin Terminal installer: lifecycle."""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Callable, Mapping
from pathlib import Path

from .cli import (
    build_paths,
    terminal_input,
    validate_args,
    validate_sync_args,
)
from .commands import CommandRunner, check_dependencies
from .common import (
    ADAPTER_SERVICE_NAME,
    SERVICE_NAME,
    ProfileLock,
    SecretRedactor,
    ToolError,
    resolved_path,
    systemd_quote,
)
from .credentials import (
    configure_gpt_actions_token,
    configure_mcp_auth_token,
    hidden_input,
    report_service_token_paths,
    resolve_service_tokens,
    validate_distinct_service_tokens,
)
from .encryption import bootstrap_e2ee
from .managed import (
    configure_lingering,
    purge_installation,
    purge_systemctl_path,
    validate_purge_paths,
)
from .models import GITHUB_OAUTH_FIELDS, SYNC_OPTIONS, Dependencies
from .oauth import (
    complete_oauth_options,
    prepare_oauth_service,
    reconcile_owned_oauth,
    save_oauth_options,
    smoke_test_oauth_service,
)
from .operations import backup_upgrade_files, dry_run_plan, dry_run_purge
from .prompts import SetupPrompts, complete_setup_options, complete_sync_options
from .releases import (
    download_mcp_binary,
    install_or_update_joplin,
    install_or_update_mcp,
    installed_joplin_version,
    installed_mcp_version,
    mcp_asset_name,
    resolve_latest_mcp_version,
    resolve_latest_version,
)
from .service import (
    deploy_supervisor,
    install_adapter_unit,
    install_unit,
    installed_service_options,
    service_active,
    smoke_test_gpt_actions_service,
    smoke_test_mcp_service,
    stop_after_startup_failure,
    systemctl_command,
    wait_for_api,
    wait_for_mcp,
)
from .settings import restore_installer_choices, save_installer_choices
from .sync import (
    authenticate_saml,
    clear_unused_sync_settings,
    configure_profile,
    extract_api_token,
    read_profile_settings,
    read_setting,
    resolve_sync_configuration,
    run_browser_authenticated_sync,
    run_sync,
    write_setting,
)

LOG = logging.getLogger("joplin-terminal-installer")


class Installer:
    def __init__(
        self,
        args: argparse.Namespace,
        *,
        env: Mapping[str, str] | None = None,
        input_fn: Callable[[str], str] = terminal_input,
        getpass_fn: Callable[[str], str] = hidden_input,
        redactor: SecretRedactor | None = None,
    ) -> None:
        self.args = args
        self.env = dict(os.environ if env is None else env)
        self.input_fn = input_fn
        self.getpass_fn = getpass_fn
        self.redactor = SecretRedactor() if redactor is None else redactor
        self.redactor.add(args.sync_username)
        self.runner = CommandRunner(self.redactor, self.env)
        self.paths = build_paths(args, self.env)
        self._upgrade_changed = False
        self._prepared_mcp: Path | None = None

    def _recover_setup(self) -> bool:
        terminal_installed = self.paths.unit_path.is_file()
        adapter_installed = self.paths.adapter_unit_path.is_file()
        if terminal_installed != adapter_installed and not self.args.purge:
            raise ToolError(
                "incomplete installed service pair; restore the missing unit or use --clean "
                "with the existing --profile-dir and --joplin-prefix before reinstalling"
            )
        if (
            terminal_installed != adapter_installed
            and self.args.purge
            and not {"profile_dir", "joplin_prefix"} <= self.args.explicit_options
        ):
            raise ToolError(
                "cleaning an incomplete service pair requires the existing --profile-dir "
                "and --joplin-prefix so unrelated default paths are not removed"
            )
        installed = terminal_installed and adapter_installed
        self.args.installed_oauth = False
        self.args.update_sync = bool(
            self.args.force_reconfigure or self.args.explicit_options & SYNC_OPTIONS
        )
        if not installed:
            if self.args.upgrade and not self.args.purge:
                raise ToolError(
                    "--upgrade requires an existing Joplin and joplin-md-sync service installation"
                )
            return False
        if self.args.purge and {"profile_dir", "joplin_prefix"} <= self.args.explicit_options:
            return True
        try:
            options = installed_service_options(self.paths)
        except ToolError:
            if not self.args.purge:
                raise
            raise ToolError(
                "cannot recover installed service paths; --clean requires the existing "
                "explicit --profile-dir and --joplin-prefix; no paths were removed"
            ) from None
        self.args.installed_oauth = bool(options.get("github_oauth"))
        for name, value in options.items():
            if name not in self.args.explicit_options:
                if not self.args.force_reconfigure or name in {"profile_dir", "joplin_prefix"}:
                    setattr(self.args, name, value)
            elif (
                name in ("profile_dir", "joplin_prefix")
                and not self.args.purge
                and resolved_path(getattr(self.args, name)) != resolved_path(str(value))
            ):
                raise ToolError(
                    "an update cannot relocate the installed profile or prefix; use a separate installation"
                )
        self.paths = build_paths(self.args, self.env)
        if not self.args.purge:
            restore_installer_choices(self.paths, self.args)
            self.args.upgrade = True
        return True

    def _configure_sync(self) -> None:
        current = read_profile_settings(self.runner, self.paths, self.redactor)
        complete_sync_options(self.args, SetupPrompts(self.args, self.input_fn), current)
        validate_sync_args(self.args)
        configuration = resolve_sync_configuration(
            self.args, self.redactor, getpass_fn=self.getpass_fn
        )
        self.args.extra_write_paths = ()
        configure_profile(self.runner, self.paths, self.args, configuration, input_fn=self.input_fn)
        clear_unused_sync_settings(
            self.runner,
            self.paths,
            current,
            configuration,
            reset=self.args.force_reconfigure,
        )
        target = configuration.target
        if target.target_id == 0:
            LOG.info("synchronization is disabled")
            return
        if target.browser_auth and not self.args.saved_browser_auth:
            run_browser_authenticated_sync(self.runner, self.paths, target)
        else:
            if target.saml_auth:
                authenticate_saml(
                    self.runner,
                    self.paths,
                    configuration,
                    self.redactor,
                    reuse_existing=self.args.saved_browser_auth,
                    input_fn=self.input_fn,
                    getpass_fn=self.getpass_fn,
                )
            run_sync(self.runner, self.paths, f"verifying {target.label} sync")
        if bootstrap_e2ee(
            self.runner, self.paths, self.args, self.redactor, getpass_fn=self.getpass_fn
        ):
            run_sync(self.runner, self.paths, "performing post-decryption sync")

    def _finish_configuration(self, dependencies: Dependencies) -> None:
        reconcile_owned_oauth(self.paths, self.args)
        save_oauth_options(self.paths, self.args)
        save_installer_choices(self.paths, self.args)
        if (
            not self.args.upgrade
            or self.args.force_reconfigure
            or "no_enable_service" in self.args.explicit_options
        ):
            action = "disable" if self.args.no_enable_service else "enable"
            for unit in (SERVICE_NAME, ADAPTER_SERVICE_NAME):
                systemctl_command(self.runner, dependencies.systemctl, action, unit)

    def _restart_and_smoke(
        self,
        dependencies: Dependencies,
        mcp_auth_token: str,
        gpt_actions_token: str,
    ) -> None:
        systemctl_command(self.runner, dependencies.systemctl, "restart", SERVICE_NAME)
        try:
            if not service_active(self.runner, dependencies.systemctl, SERVICE_NAME):
                raise ToolError(f"{SERVICE_NAME} is not active after restart")
            wait_for_api(self.args.api_port)
        except ToolError:
            stop_after_startup_failure(
                self.runner,
                dependencies.systemctl,
                SERVICE_NAME,
            )
            raise

        systemctl_command(
            self.runner,
            dependencies.systemctl,
            "restart",
            ADAPTER_SERVICE_NAME,
        )
        try:
            if not service_active(self.runner, dependencies.systemctl, ADAPTER_SERVICE_NAME):
                raise ToolError(f"{ADAPTER_SERVICE_NAME} is not active after restart")
            wait_for_mcp(self.args.mcp_port, mcp_auth_token)
            smoke_test_mcp_service(self.args.mcp_port, mcp_auth_token)
            smoke_test_gpt_actions_service(
                self.args.mcp_port,
                gpt_actions_token,
            )
            if getattr(self.args, "github_oauth", False):
                smoke_test_oauth_service(self.args.mcp_port, self.args.github_oauth_public_base_url)
        except ToolError:
            stop_after_startup_failure(
                self.runner,
                dependencies.systemctl,
                ADAPTER_SERVICE_NAME,
            )
            raise

    def _upgrade(
        self,
        dependencies: Dependencies,
        mcp_auth_token: str,
        gpt_actions_token: str,
    ) -> None:
        if not self.paths.unit_path.is_file() or not self.paths.adapter_unit_path.is_file():
            raise ToolError(
                "--upgrade requires an existing Joplin and joplin-md-sync service installation"
            )
        joplin_version = (
            resolve_latest_version(self.runner, dependencies.npm)
            if self.args.joplin_version == "latest"
            else self.args.joplin_version
        )
        mcp_version = (
            resolve_latest_mcp_version()
            if self.args.joplin_md_sync_version == "latest"
            else self.args.joplin_md_sync_version
        )
        # Download, verify and smoke-test a changed adapter before stopping
        # healthy services, so a release or network failure leaves them running.
        if installed_mcp_version(self.runner, self.paths.mcp_binary) != mcp_version:
            self._prepared_mcp = download_mcp_binary(
                self.runner, self.paths, mcp_version, mcp_asset_name()
            )
        if getattr(self.args, "github_oauth", False):
            prepare_oauth_service(
                self.runner,
                self.paths,
                self.args,
                binary=self._prepared_mcp or self.paths.mcp_binary,
                provision=False,
            )
        LOG.info(
            "upgrade targets: Joplin %s; joplin-md-sync %s",
            joplin_version,
            mcp_version,
        )
        self._upgrade_changed = True
        if service_active(self.runner, dependencies.systemctl, ADAPTER_SERVICE_NAME):
            systemctl_command(self.runner, dependencies.systemctl, "stop", ADAPTER_SERVICE_NAME)
        if service_active(self.runner, dependencies.systemctl, SERVICE_NAME):
            systemctl_command(self.runner, dependencies.systemctl, "stop", SERVICE_NAME)
        with ProfileLock(self.paths.lock_file):
            backup = backup_upgrade_files(self.paths)
            # npm otherwise overwrites the only copy of the previous executable.
            # Move only the isolated managed package tree; never copy a profile.
            if (
                self.paths.npm_prefix.is_dir()
                and installed_joplin_version(self.runner, dependencies.npm, self.paths.npm_prefix)
                != joplin_version
            ):
                os.replace(self.paths.npm_prefix, backup / "npm")
            installed_joplin = install_or_update_joplin(
                self.runner,
                dependencies,
                self.paths,
                joplin_version,
            )
            LOG.info("verified Joplin %s", installed_joplin)
            if self._prepared_mcp is not None:
                os.replace(self._prepared_mcp, self.paths.mcp_binary)
                self.paths.mcp_binary.chmod(0o700)
                self._prepared_mcp = None
                installed_mcp = mcp_version
            else:
                installed_mcp = install_or_update_mcp(self.runner, self.paths, mcp_version)
            LOG.info("verified joplin-md-sync standalone %s", installed_mcp)
            validate_distinct_service_tokens(
                self.paths,
                gpt_actions_token,
                mcp_auth_token,
            )
            configure_mcp_auth_token(self.paths, mcp_auth_token)
            configure_gpt_actions_token(self.paths, gpt_actions_token)
            prepare_oauth_service(self.runner, self.paths, self.args)
            if self.args.update_sync:
                self._configure_sync()
            if getattr(self.args, "recover_filesystem_target", False):
                target = read_setting(self.runner, self.paths, "sync.target").split(" ", 1)[0]
                if target == "2":
                    self.args.extra_write_paths = (
                        resolved_path(read_setting(self.runner, self.paths, "sync.2.path")),
                    )
            if "api_port" in self.args.explicit_options:
                write_setting(self.runner, self.paths, "api.port", str(self.args.api_port))
            if "sync_interval" in self.args.explicit_options:
                write_setting(
                    self.runner, self.paths, "sync.interval", str(self.args.sync_interval)
                )
            supervisor, _common = deploy_supervisor(self.paths)
            install_unit(self.paths, self.args, supervisor, dependencies.node)
            install_adapter_unit(self.paths, self.args)
        systemctl_command(self.runner, dependencies.systemctl, "daemon-reload")
        self._finish_configuration(dependencies)
        if self.args.no_start_service:
            LOG.info("services remain stopped due to --no-start-service")
            return
        self._restart_and_smoke(dependencies, mcp_auth_token, gpt_actions_token)
        LOG.info("Joplin Terminal and joplin-md-sync service upgrade completed")

    def run(self) -> None:
        try:
            self._run()
        except BaseException:
            created = getattr(self.args, "new_oauth_files", ())
            if created:
                # Keep generated secrets only when the installed unit uses them;
                # never let this cleanup replace the original failure.
                try:
                    unit = self.paths.adapter_unit_path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    unit = ""
                for path in created:
                    if systemd_quote(path) not in unit:
                        path.unlink(missing_ok=True)
            raise

    def _run(self) -> None:
        if (
            not self.args.purge
            and self.args.no_github_oauth
            and "github_oauth" in self.args.command_line_options
        ):
            raise ToolError("--github-oauth cannot be combined with --no-github-oauth")
        installed = self._recover_setup()
        if self.args.no_github_oauth:
            if (
                self.args.installed_oauth
                and not self.args.force_reconfigure
                and not self.args.dry_run
                and not self.args.purge
            ):
                save_oauth_options(self.paths, self.args)
            self.args.github_oauth = False
            for field in GITHUB_OAUTH_FIELDS:
                setattr(self.args, "github_oauth_" + field, None)
        if self.args.purge:
            validate_args(self.args)
            LOG.info("Joplin profile selected for purge: %s", self.paths.profile_dir)
            if self.args.dry_run:
                dry_run_purge(self.paths, self.env)
                return
            validate_purge_paths(self.paths, self.env)
            purge_installation(
                self.runner,
                purge_systemctl_path(),
                self.paths,
                self.env,
            )
            return
        previous_paths = self.paths
        complete_setup_options(self.args, self.paths, installed=installed, input_fn=self.input_fn)
        self.paths = build_paths(self.args, self.env)
        if installed and (
            self.paths.prefix != previous_paths.prefix
            or self.paths.profile_dir != previous_paths.profile_dir
        ):
            raise ToolError("an update cannot relocate the installed profile or prefix")
        complete_oauth_options(
            self.args, self.paths, self.redactor, input_fn=self.input_fn, getpass_fn=self.getpass_fn
        )
        validate_args(self.args)
        dependencies = check_dependencies(self.runner)
        LOG.info("isolated npm prefix: %s", self.paths.npm_prefix)
        LOG.info("Joplin profile: %s", self.paths.profile_dir)
        LOG.info("Joplin Data API port: %d", self.args.api_port)
        mcp_host = "0.0.0.0" if self.args.allow_remote_mcp else "127.0.0.1"
        LOG.info(
            "joplin-md-sync listener: http://%s:%d (MCP /mcp; GPT Actions /api/gpt/v1/*)",
            mcp_host,
            self.args.mcp_port,
        )
        if self.args.dry_run:
            dry_run_plan(self.args, self.paths)
            return
        for name in ("JOPLIN_GPT_ACTIONS_TOKEN", "JOPLIN_MCP_AUTH_TOKEN"):
            if self.env.get(name):
                LOG.warning("%s is ignored; the installer manages service tokens", name)
        mcp_auth_token, gpt_actions_token = resolve_service_tokens(
            self.paths,
            self.redactor,
        )
        if self.args.upgrade:
            if "enable_linger" in self.args.explicit_options:
                configure_lingering(
                    self.runner, dependencies.loginctl, self.args, input_fn=self.input_fn
                )
            try:
                self._upgrade(dependencies, mcp_auth_token, gpt_actions_token)
            except BaseException:
                # Any failure, including an unexpected one, must not leave the
                # new backend restarting or modifying a possibly migrated profile.
                if self._upgrade_changed:
                    for unit in (ADAPTER_SERVICE_NAME, SERVICE_NAME):
                        stop_after_startup_failure(self.runner, dependencies.systemctl, unit)
                    LOG.error(
                        "upgrade failed; any previous files remain under %s/upgrade-backup-*; "
                        "services are stopped, profile rollback is never automatic",
                        self.paths.state_dir,
                    )
                raise
            finally:
                if self._prepared_mcp is not None:
                    self._prepared_mcp.unlink(missing_ok=True)
                    self._prepared_mcp = None
            report_service_token_paths(self.paths)
            del mcp_auth_token
            del gpt_actions_token
            return

        configure_lingering(
            self.runner,
            dependencies.loginctl,
            self.args,
            input_fn=self.input_fn,
        )
        terminal_was_active = service_active(
            self.runner,
            dependencies.systemctl,
            SERVICE_NAME,
        )
        adapter_was_active = service_active(
            self.runner,
            dependencies.systemctl,
            ADAPTER_SERVICE_NAME,
        )
        if adapter_was_active:
            LOG.info("stopping active %s", ADAPTER_SERVICE_NAME)
            systemctl_command(
                self.runner,
                dependencies.systemctl,
                "stop",
                ADAPTER_SERVICE_NAME,
            )
        if terminal_was_active:
            LOG.info("stopping active %s for exclusive profile access", SERVICE_NAME)
            systemctl_command(self.runner, dependencies.systemctl, "stop", SERVICE_NAME)

        units_changed = False
        with ProfileLock(self.paths.lock_file):
            version = install_or_update_joplin(
                self.runner,
                dependencies,
                self.paths,
                self.args.joplin_version,
            )
            LOG.info("verified Joplin %s", version)
            self._configure_sync()
            extract_api_token(self.runner, self.paths, self.redactor)
            mcp_installed_version = install_or_update_mcp(
                self.runner,
                self.paths,
                self.args.joplin_md_sync_version,
            )
            LOG.info(
                "verified joplin-md-sync standalone %s",
                mcp_installed_version,
            )
            validate_distinct_service_tokens(
                self.paths,
                gpt_actions_token,
                mcp_auth_token,
            )
            configure_mcp_auth_token(self.paths, mcp_auth_token)
            configure_gpt_actions_token(self.paths, gpt_actions_token)
            prepare_oauth_service(self.runner, self.paths, self.args)
            supervisor, _common = deploy_supervisor(self.paths)
            joplin_unit_changed = install_unit(
                self.paths,
                self.args,
                supervisor,
                dependencies.node,
            )
            adapter_unit_changed = install_adapter_unit(self.paths, self.args)
            units_changed = joplin_unit_changed or adapter_unit_changed

        # Reload even when content is unchanged: the user manager may have restarted.
        systemctl_command(self.runner, dependencies.systemctl, "daemon-reload")
        self._finish_configuration(dependencies)
        if units_changed:
            LOG.info("systemd service definitions changed")
        if self.args.no_start_service:
            LOG.info("services were not started due to --no-start-service")
        else:
            self._restart_and_smoke(
                dependencies,
                mcp_auth_token,
                gpt_actions_token,
            )
        LOG.info("Joplin Terminal and joplin-md-sync service installation completed")
        report_service_token_paths(self.paths)
        del mcp_auth_token
        del gpt_actions_token
