# PICK POCKET MOBILE RADIO CONTROL — TALK mix
# Liquidsoap 2.3.x/2.4.x compatible pattern.
#
# IMPORTANT:
# - `music` below means your existing SONO PLAY MINI LIVE program source.
# - Do not create a second public output. Feed `radio_with_talk` into the
#   existing output.icecast that already serves /stream.mp3.

# Temporary voice source produced by backend/talk_server.py.
talk = input.http(
  id="pocket_talk",
  buffer=0.15,
  max=1.0,
  "http://127.0.0.1:8000/pocket-talk.mp3"
)

# Voice stays at full level. Music is reduced while TALK is actually ready.
# 0.22 ~= -13 dB, enough for speech without muting the station completely.
talk_mix = add(normalize=false, [talk, amplify(0.22, music)])

# track_sensitive=false makes push-to-talk immediate instead of waiting for
# the end of the current song.
radio_with_talk = switch(
  track_sensitive=false,
  [
    ({talk.is_ready()}, talk_mix),
    ({true}, music)
  ]
)

# Example only — keep your real Icecast password/output parameters unchanged:
# output.icecast(%mp3(bitrate=128), mount="stream.mp3", radio_with_talk)
