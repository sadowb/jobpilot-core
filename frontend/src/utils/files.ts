export const MAX_CV_BYTES = 3_000_000;

/** File -> base64 (no data: prefix). Rejects early with a message the owner can act on. */
export function readBase64(file: File, limit = MAX_CV_BYTES): Promise<string> {
  if (file.size > limit)
    return Promise.reject(
      new Error(`${file.name} is too large (max ${Math.round(limit / 1_000_000)} MB).`),
    );
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
    reader.onload = () => {
      const result = String(reader.result ?? '');
      const comma = result.indexOf(',');
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.readAsDataURL(file);
  });
}

/** UTF-8 text -> base64 (btoa alone breaks on non-Latin-1 characters). */
export function textToBase64(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}
