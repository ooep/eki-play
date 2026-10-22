#!/bin/sh
# Run git against /data/eki-play with credentials supplied through GIT_ASKPASS,
# so the token never appears in a remote URL, in .git/config, or in command output.
cat > /tmp/eki_askpass.sh <<'EOS'
#!/bin/sh
case "$1" in
  *sername*) echo x-access-token ;;
  *assword*) cat /data/work/.gh_token ;;
  *)         cat /data/work/.gh_token ;;
esac
EOS
chmod 700 /tmp/eki_askpass.sh
GIT_ASKPASS=/tmp/eki_askpass.sh GIT_TERMINAL_PROMPT=0 exec git -C /data/eki-play "$@"
