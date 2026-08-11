"""One controller per service, both exposing the SAME methods, so every front end is mode-agnostic.

`play_pause pause resume next_track previous_track toggle_like show_current` -- and all of them
return the TEXT to show rather than raising, because they run on the hotkey thread where an escaped
exception is a silently dead key. `last_error` is how a caller tells a failure from a track name.
"""
