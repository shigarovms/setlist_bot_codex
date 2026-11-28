const identifier = "[0-9a-f]{32}";
const roleLink = new RegExp(`^team_(${identifier})$`);
const workspaceLink = new RegExp(
  `^ws_([a-z]+)_(${identifier})(?:_(${identifier}))?(?:_(${identifier}))?$`,
);
const setlistTargets = { variant: "variant", request: "decision", approved: "revision" };
const uuid = value => value.replace(/^(.{8})(.{4})(.{4})(.{4})(.{12})$/, "$1-$2-$3-$4-$5");

export function workspaceLaunch(start = "") {
  const legacy = roleLink.exec(start);
  if (legacy) return { band: legacy[1], view: "team", roles: "1" };
  const match = workspaceLink.exec(start);
  if (!match) return {};
  const [, section, band, performance, target] = match;
  if (section === "catalog" && !performance) return { band, view: "repertoire" };
  if (section === "songs" && !target) {
    const proposal = performance ? uuid(performance) : null;
    return {
      band, view: "repertoire", proposals: "1",
      ...(proposal ? { proposal, focus: `catalog-${proposal}` } : {}),
    };
  }
  if (section === "event" && performance && !target) return { band, performance, view: "performance" };
  if (target && Object.hasOwn(setlistTargets, section)) return {
    band, performance, view: "performance", [setlistTargets[section]]: uuid(target),
  };
  return {};
}
