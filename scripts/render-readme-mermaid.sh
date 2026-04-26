#!/usr/bin/env bash
# Render Mermaid in README.md to docs/images/*.svg (same rules as
# nielsvaneck/render-md-mermaid@v3: image markdown, then <details>, then ```mermaid).
# Requires Docker. Override image with MERMAID_CLI_IMAGE if needed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
IMG="${MERMAID_CLI_IMAGE:-minlag/mermaid-cli:latest}"
export ROOT IMG
perl -0777 -e '
  use strict;
  use warnings;
  open my $fh, "<", "$ENV{ROOT}/README.md" or die $!;
  local $/;
  my $doc = <$fh>;
  close $fh;
  my $n = 0;
  while ($doc =~ m/!\[.*?\]\(([^\)]+)\)\n+<details>([\s\S]*?)```mermaid\n([\s\S]*?)\n```/g) {
    $n++;
    my ($rel, $src) = ($1, $3);
    open my $m, ">", "/tmp/homelab-mermaid-$n.mmd" or die $!;
    print {$m} $src;
    close $m;
    my $out = "/tmp/homelab-mermaid-out-$n.svg";
    unlink $out if -e $out;
    my @cmd = (
      "docker", "run", "--rm", "-v/tmp:/t", $ENV{IMG},
      "-i", "/t/homelab-mermaid-$n.mmd", "-o", "/t/homelab-mermaid-out-$n.svg", "-t", "neutral",
    );
    system(@cmd) == 0 or die "mmdc failed for $rel (exit " . ($? >> 8) . ")\n";
    die "missing $out\n" unless -e $out;
    my $dest = "$ENV{ROOT}/$rel";
    $dest =~ m{^$ENV{ROOT}/docs/images/[^/]+\.svg$}
      or die "refusing to write outside docs/images: $rel\n";
    system("cp", "--", $out, $dest) == 0 or die "cp $rel failed\n";
    print "Generated $rel\n";
    unlink $out;
  }
  die "no Mermaid diagrams matched in README.md\n" unless $n;
'
