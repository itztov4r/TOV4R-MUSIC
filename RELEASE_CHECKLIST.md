# TOV4R V5.0 — release gate

Release candidate, not a completed public launch.

- [ ] Latest GitHub Actions workflow passes on the reconciled branch.
- [ ] Validate live Discord global commands, /dev badges and /native_automod preview in a test server.
- [ ] Create one temporary TAB, verify the panel in that room's chat, verify a non-owner cannot edit it, and confirm empty-room cleanup.
- [ ] Verify OAuth login, authorized server filtering, module toggles and unauthorized AI-setting rejection in a test server.
- [ ] Verify existing private support DM setup is preserved.
- [ ] Check optional /ai only after explicit owner enablement and provider configuration. Public AI conversation and voice AI are not shipped in this candidate.
- [ ] Operator approves privacy notice and terms, provides a verified contact, and confirms actual data retention and deletion processes.
- [ ] Confirm Railway volume and variables remain intact; deploy only to the existing service after checks. Verify deployment SUCCESS, gateway logs and public status.
- [ ] Verify live functionality and label only verified features as released. Keep voice AI out of public claims until consent, safety, and end-to-end audio tests are implemented.

Rollback: restore the last known good production commit/service deployment if startup fails. Do not delete the data volume or reset credentials.
