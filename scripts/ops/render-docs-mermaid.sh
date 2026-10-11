#!/usr/bin/env bash
# Render Mermaid diagrams in one or more Markdown files (image + details + ```mermaid pattern).
# Usage: bash scripts/ops/render-docs-mermaid.sh README.md docs/architecture.md docs/operations.md
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
IMG="${MERMAID_CLI_IMAGE:-minlag/mermaid-cli:latest}"
export ROOT IMG

render_file() {
  local md="$1"
  [[ -f "$md" ]] || { echo "missing: $md" >&2; return 1; }
  export MD="$md"
  perl -0777 -e '
    use strict; use warnings;
    use File::Basename;
    open my $fh, "<", "$ENV{MD}" or die $!;
    local $/; my $doc = <$fh>; close $fh;
    my $md_dir = dirname($ENV{MD});
    my $n = 0;
    while ($doc =~ m/!\[.*?\]\(([^\)]+)\)\n+<details>([\s\S]*?)```mermaid\n([\s\S]*?)\n```/g) {
      $n++;
      my ($rel, $src) = ($1, $3);
      open my $m, ">", "/tmp/homelab-mermaid-$n.mmd" or die $!;
      print {$m} $src; close $m;
      my $out = "/tmp/homelab-mermaid-out-$n.svg";
      unlink $out if -e $out;
      my @cmd = ("docker","run","--rm","-v/tmp:/t",$ENV{IMG},
        "-i","/t/homelab-mermaid-$n.mmd","-o","/t/homelab-mermaid-out-$n.svg","-t","neutral");
      system(@cmd) == 0 or die "mmdc failed for $rel\n";
      my $dest = "$ENV{ROOT}/$md_dir/$rel";
      # Normalize dest (remove ./ and resolve ../)
      $dest =~ s{/\./}{/}g;
      while ($dest =~ s{/[^/]+/\.\./}{/}) {}
      $dest =~ m{^$ENV{ROOT}/docs/images/[^/]+\.svg$} or die "refusing: $rel (resolved to $dest)\n";
      system("cp","--",$out,$dest)==0 or die "cp failed\n";
      print "Generated $rel from $ENV{MD}\n";
      unlink $out;
    }
    print "No diagrams in $ENV{MD}\n" unless $n;
  '
}

if [[ $# -eq 0 ]]; then
  set -- README.md docs/architecture.md docs/operations.md
fi
for f in "$@"; do render_file "$f"; done
