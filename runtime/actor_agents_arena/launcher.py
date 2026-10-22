"""Ordinary Agents Arena client flags; the generic launcher knows no game UI."""
from actor_runtime.launching import GameLaunchProfile
from actor_runtime.errors import ActorError
import unicodedata


def arguments(*,room,player_name,endpoint,report,log,headless,api_url):
    # Match JoinTicketCodec.IsPlayerNameValid; game rules stay in this plugin.
    # C# checks UTF-16 chars, so supplementary-plane letters are not accepted.
    if (not 2 <= len(player_name) <= 24 or player_name != player_name.strip() or
            any(character not in " ._-" and (ord(character) > 0xffff or
                unicodedata.category(character) not in {"Lu","Ll","Lt","Lm","Lo","Nd"})
                for character in player_name)):
        raise ActorError("player_name_invalid","Agents Arena names use 2–24 letters, decimal digits, spaces, dots, underscores or hyphens")
    result=["--auto-room",room,"--player-name",player_name,"--control-mode","actor",
        "--actor-endpoint",endpoint,"--actor-report",str(report),
        "-screen-fullscreen","0","-screen-width","800","-screen-height","600","-logFile",str(log)]
    if headless:result += ["-batchmode","-nographics"]
    if api_url:result += ["--api-url",api_url]
    return result


def game_profile():
    return GameLaunchProfile(game_id="agents-arena",game_version="0.10.0",match_policy="agents-arena",
        default_brain="codex",client_candidates=("build/agents-arena-client/AgentsArena.app/Contents/MacOS/Agents Arena",
        "/Applications/AgentsArena.app/Contents/MacOS/Agents Arena"),client_arguments=arguments)
