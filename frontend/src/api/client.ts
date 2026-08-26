export async function readJson(response: Response, operation: string): Promise<unknown> {
  if (!response.ok) {
    let detail = ''
    try {
      const body = (await response.json()) as { detail?: unknown }
      detail = typeof body.detail === 'string' ? ` ${body.detail}` : ''
    } catch {
      // The status and endpoint still provide a precise error when the body is not JSON.
    }
    throw new Error(`${operation} returned ${response.status}.${detail}`)
  }
  return response.json() as Promise<unknown>
}
