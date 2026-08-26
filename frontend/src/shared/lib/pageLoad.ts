/** True when a page should replace body content instead of showing stale or empty data. */
export function isContentLoading(loading: boolean, data: unknown): boolean {
  return loading && data == null;
}
