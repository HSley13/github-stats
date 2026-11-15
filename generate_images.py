#!/usr/bin/python3

import asyncio
import glob
import html
import os
import re
from typing import List, Tuple

import aiohttp

from github_stats import Queries, Stats


################################################################################
# Helper Functions
################################################################################


def generate_output_folder() -> None:
    """
    Create the output folder if it does not already exist
    """
    if not os.path.isdir("generated"):
        os.mkdir("generated")


def read_pinned_repos(user: str) -> List[Tuple[str, str]]:
    """
    Read the list of repositories to generate pin cards for
    :param user: default owner for entries written without an owner
    :return: list of (owner, name) pairs
    """
    if not os.path.isfile("pinned_repos.txt"):
        return []
    repos = []
    with open("pinned_repos.txt", "r") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            owner, _, name = line.rpartition("/")
            owner = owner or user
            if not re.fullmatch(r"[\w.-]+", owner) or not re.fullmatch(r"[\w.-]+", name):
                print(f"Skipping invalid entry in pinned_repos.txt: {line}")
                continue
            if (owner.lower(), name.lower()) not in {
                (o.lower(), n.lower()) for o, n in repos
            }:
                repos.append((owner, name))
    return repos


################################################################################
# Individual Image Generation Functions
################################################################################


async def generate_overview(s: Stats) -> None:
    """
    Generate an SVG badge with summary statistics
    :param s: Represents user's GitHub statistics
    """
    with open("templates/overview.svg", "r") as f:
        output = f.read()

    output = re.sub("{{ name }}", await s.name, output)
    output = re.sub("{{ stars }}", f"{await s.stargazers:,}", output)
    output = re.sub("{{ forks }}", f"{await s.forks:,}", output)
    output = re.sub("{{ contributions }}", f"{await s.total_contributions:,}", output)
    changed = (await s.lines_changed)[0] + (await s.lines_changed)[1]
    output = re.sub("{{ lines_changed }}", f"{changed:,}", output)
    output = re.sub("{{ views }}", f"{await s.views:,}", output)
    output = re.sub("{{ repos }}", f"{len(await s.repos):,}", output)

    generate_output_folder()
    with open("generated/overview.svg", "w") as f:
        f.write(output)


async def generate_languages(s: Stats) -> None:
    """
    Generate an SVG badge with summary languages used
    :param s: Represents user's GitHub statistics
    """
    with open("templates/languages.svg", "r") as f:
        output = f.read()

    progress = ""
    lang_list = ""
    sorted_languages = sorted(
        (await s.languages).items(), reverse=True, key=lambda t: t[1].get("size")
    )
    delay_between = 150
    for i, (lang, data) in enumerate(sorted_languages):
        color = data.get("color")
        color = color if color is not None else "#000000"
        progress += (
            f'<span style="background-color: {color};'
            f'width: {data.get("prop", 0):0.3f}%;" '
            f'class="progress-item"></span>'
        )
        lang_list += f"""
<li style="animation-delay: {i * delay_between}ms;">
<svg xmlns="http://www.w3.org/2000/svg" class="octicon" style="fill:{color};"
viewBox="0 0 16 16" version="1.1" width="16" height="16"><path
fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8z"></path></svg>
<span class="lang">{lang}</span>
<span class="percent">{data.get("prop", 0):0.2f}%</span>
</li>

"""

    output = re.sub(r"{{ progress }}", progress, output)
    output = re.sub(r"{{ lang_list }}", lang_list, output)

    generate_output_folder()
    with open("generated/languages.svg", "w") as f:
        f.write(output)


async def generate_pins(queries: Queries, user: str) -> None:
    """
    Generate an SVG card for each repository listed in pinned_repos.txt, plus
    a Markdown snippet linking every card to its repository
    :param queries: object used to query the GitHub API
    :param user: default owner for entries written without an owner
    """
    repos = read_pinned_repos(user)

    generate_output_folder()
    if not os.path.isdir("generated/pins"):
        os.mkdir("generated/pins")
    # Remove cards for repositories no longer in the list
    for path in glob.glob("generated/pins/*.svg"):
        os.remove(path)
    if not repos:
        return

    with open("templates/pin.svg", "r") as f:
        template = f.read()

    result = await queries.query(Queries.repos_info(repos))
    data = result.get("data") or {}
    for error in result.get("errors", []):
        print(f"Pinned repo error: {error.get('message')}")

    stats_repo = os.getenv("GITHUB_REPOSITORY", f"{user}/github-stats")
    snippet = ""
    for i, (owner, name) in enumerate(repos):
        repo = data.get(f"repo{i}")
        if repo is None:
            print(f"Skipping {owner}/{name}: repository not found")
            continue

        language = repo.get("primaryLanguage")
        language_html = ""
        if language is not None:
            color = language.get("color") or "#000000"
            language_html = (
                f'<span><span class="dot" style="background-color: {color};">'
                f"</span>{html.escape(language['name'])}</span>"
            )
        description = repo.get("description") or "No description provided."
        display_name = name if owner.lower() == user.lower() else f"{owner}/{name}"

        output = re.sub("{{ name }}", lambda _: html.escape(display_name), template)
        output = re.sub(
            "{{ description }}", lambda _: html.escape(description), output
        )
        output = re.sub("{{ language }}", lambda _: language_html, output)
        output = re.sub("{{ stars }}", f"{repo['stargazerCount']:,}", output)
        output = re.sub("{{ forks }}", f"{repo['forkCount']:,}", output)

        file_name = f"{owner}-{name}.svg"
        with open(f"generated/pins/{file_name}", "w") as f:
            f.write(output)

        image = f"https://github.com/{stats_repo}/blob/master/generated/pins/{file_name}"
        snippet += (
            f'<a href="{repo["url"]}">'
            f'<img src="{image}#gh-dark-mode-only" />'
            f'<img src="{image}#gh-light-mode-only" /></a>\n'
        )

    with open("generated/pins.md", "w") as f:
        f.write(snippet)


################################################################################
# Main Function
################################################################################


async def main() -> None:
    """
    Generate all badges
    """
    access_token = os.getenv("ACCESS_TOKEN")
    if not access_token:
        # access_token = os.getenv("GITHUB_TOKEN")
        raise Exception("A personal access token is required to proceed!")
    user = os.getenv("GITHUB_ACTOR")
    if user is None:
        raise RuntimeError("Environment variable GITHUB_ACTOR must be set.")
    exclude_repos = os.getenv("EXCLUDED")
    excluded_repos = (
        {x.strip() for x in exclude_repos.split(",")} if exclude_repos else None
    )
    exclude_langs = os.getenv("EXCLUDED_LANGS")
    excluded_langs = (
        {x.strip() for x in exclude_langs.split(",")} if exclude_langs else None
    )
    # Convert a truthy value to a Boolean
    raw_ignore_forked_repos = os.getenv("EXCLUDE_FORKED_REPOS")
    ignore_forked_repos = (
        not not raw_ignore_forked_repos
        and raw_ignore_forked_repos.strip().lower() != "false"
    )
    async with aiohttp.ClientSession() as session:
        s = Stats(
            user,
            access_token,
            session,
            exclude_repos=excluded_repos,
            exclude_langs=excluded_langs,
            ignore_forked_repos=ignore_forked_repos,
        )
        await asyncio.gather(
            generate_languages(s),
            generate_overview(s),
            generate_pins(s.queries, user),
        )


if __name__ == "__main__":
    asyncio.run(main())
