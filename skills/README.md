# Approved skills

Commit reviewed skill folders here (typically one directory per skill, each with a SKILL.md).

This directory is mounted read-only as /opt/company-skills and registered using:

    hermes config set skills.external_dirs '["/opt/company-skills"]'
    hermes config set skills.write_approval true

Run make configure after initial setup (with the gateway stopped). Built-in/local runtime skills can take precedence over identically named external skills: run make doctor and resolve name collisions.

Never put tokens or credentials in this directory. Skills are executable instructions, so treat external contributions as code reviews.
