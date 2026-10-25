// Only known application documents may be a post-login destination.
export function loginDestination(value) {
  if (typeof value !== "string" || !value.startsWith("/") || /[\\\s]/.test(value)) return null;
  const origin = "https://atp.invalid";
  try {
    const url = new URL(value, origin);
    if (url.origin !== origin) return null;
    if (!/^\/(?:help|history|knowledge(?:\/[^/]+)?|memory-tasks\/[^/]+|runs\/[^/]+|briefings\/[^/]+)?$/.test(url.pathname)) return null;
    return url.pathname + url.search + url.hash;
  } catch {
    return null;
  }
}

export function loginPath(location) {
  const target = loginDestination(location.pathname + location.search + location.hash);
  return target ? "/login?next=" + encodeURIComponent(target) : "/login";
}
