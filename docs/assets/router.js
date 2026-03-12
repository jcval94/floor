export function initRouter() {
  const params = new URLSearchParams(window.location.search);
  return {
    ticker: params.get('ticker') || null,
    section: params.get('section') || null,
  };
}
