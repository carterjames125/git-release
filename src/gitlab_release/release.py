"""Tag creation, artifact upload, and release creation - the mutating sequence behind
`release --no-dry-run`. Business logic, callable without Click, per the layering rule."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from gitlab_release import artifacts, changelog
from gitlab_release.config import ArtifactSettings, Settings
from gitlab_release.errors import ArtifactError, ConfigError, GitLabAPIError
from gitlab_release.gitlab_client import GitlabClient


@dataclass(frozen=True)
class ReleaseResult:
    tag_created: bool
    release_url: str | None
    package_urls: list[dict[str, str]]
    changelog_text: str


def execute(
    client: GitlabClient,
    settings: Settings,
    *,
    dry_run: bool,
    artifact_settings: ArtifactSettings | None,
    release_name: str,
    if_exists: str,
    template_path: Path | None,
) -> ReleaseResult:
    if settings.is_job_token and not dry_run:
        raise ConfigError(
            "CI_JOB_TOKEN cannot create tags. Set --token (or GITLAB_TOKEN) to a "
            "project access token or personal access token with the 'api' scope."
        )

    # The tag-existence check is a cheap, side-effect-free read, so it happens first and
    # unconditionally: a real tag conflict must abort the run before any artifact is
    # uploaded, not after. The actual (mutating) tag creation is deferred until after
    # artifacts are resolved, so an --if-exists=fail conflict on a package still aborts
    # before a tag gets created that would need manual cleanup on retry. Together these
    # two orderings mean neither the tag-exists check nor the artifact-conflict check can
    # be short-circuited by the other's mutation.
    if client.tag_exists(settings.tag):
        raise GitLabAPIError(
            f"Tag {settings.tag!r} already exists. If a prior run created it and failed "
            "later, delete the tag and re-run (add --if-exists skip if artifacts from "
            "that prior run were already uploaded). Otherwise choose a different tag."
        )

    package_urls = _handle_artifacts(
        client, artifact_settings, settings.tag, if_exists=if_exists, dry_run=dry_run
    )
    tag_created = _create_tag(client, tag=settings.tag, ref=settings.ref, dry_run=dry_run)

    context = changelog.build_context(client, settings, packages=package_urls)
    changelog_text = changelog.render(context, template_path=template_path)

    release_url = _handle_release(
        client,
        tag=settings.tag,
        release_name=release_name,
        changelog_text=changelog_text,
        package_urls=package_urls,
        if_exists=if_exists,
        dry_run=dry_run,
    )

    return ReleaseResult(
        tag_created=tag_created,
        release_url=release_url,
        package_urls=package_urls,
        changelog_text=changelog_text,
    )


def _create_tag(client: GitlabClient, *, tag: str, ref: str, dry_run: bool) -> bool:
    """Existence has already been checked (and would have raised) before this is called -
    this is the write half only, so a caller never re-checks a fact it already has."""
    if dry_run:
        return False
    client.create_tag(tag=tag, ref=ref)
    return True


def _handle_artifacts(
    client: GitlabClient,
    artifact_settings: ArtifactSettings | None,
    tag: str,
    *,
    if_exists: str,
    dry_run: bool,
) -> list[dict[str, str]]:
    if artifact_settings is None:
        return []

    paths = artifacts.discover(artifact_settings.source_path, artifact_settings.pattern)
    version = artifacts.normalize_version(tag)
    package_urls: list[dict[str, str]] = []
    for path in paths:
        file_name = path.name
        exists = client.package_file_exists(
            name=artifact_settings.package_name, version=version, file_name=file_name
        )
        if exists:
            if if_exists == "fail":
                raise ArtifactError(
                    f"Package file {file_name!r} already exists for "
                    f"{artifact_settings.package_name} {version} (--if-exists=fail)"
                )
            logger.info(
                "Package file {} already exists for {} {}; skipping upload (--if-exists={})",
                file_name,
                artifact_settings.package_name,
                version,
                if_exists,
            )
            package_urls.append(
                {
                    "name": file_name,
                    "url": client.package_download_url(
                        name=artifact_settings.package_name,
                        version=version,
                        file_name=file_name,
                    ),
                }
            )
            continue
        if dry_run:
            package_urls.append(
                {
                    "name": file_name,
                    "url": client.package_download_url(
                        name=artifact_settings.package_name,
                        version=version,
                        file_name=file_name,
                    ),
                }
            )
            continue
        url = client.upload_package_file(
            name=artifact_settings.package_name,
            version=version,
            file_name=file_name,
            path=path,
        )
        package_urls.append({"name": file_name, "url": url})
    return package_urls


def _handle_release(
    client: GitlabClient,
    *,
    tag: str,
    release_name: str,
    changelog_text: str,
    package_urls: list[dict[str, str]],
    if_exists: str,
    dry_run: bool,
) -> str | None:
    if client.release_exists(tag):
        if if_exists == "fail":
            raise GitLabAPIError(f"Release for tag {tag!r} already exists.")
        if if_exists == "update":
            raise ConfigError(
                f"Release for tag {tag!r} already exists and --if-exists=update is not "
                "supported for releases. Use --if-exists=skip instead."
            )
        logger.info("Release for tag {} already exists; skipping (--if-exists=skip)", tag)
        return None
    if dry_run:
        return None
    return client.create_release(
        tag=tag, name=release_name, description=changelog_text, links=package_urls
    )
