/**
 * The ward whiteboard, drawn in the browser so the live model has something
 * real to read.
 *
 * Spec 26.2 has a facility photograph the board beside the ward door: the bed
 * count, the occupied count, and the day's four-character verification code
 * written on it by hand. Gemini's job is to get all three back out of one
 * image, which is what makes the freshness check work — yesterday's photo
 * carries yesterday's code and fails.
 *
 * A demo has no ward and no camera, so this draws the board. It is a drawing
 * and it says so on its own face; nothing in the interface calls it a
 * photograph. What is not simulated is the part that matters: the code comes
 * from the server's own rotating-code endpoint, the image really is sent to
 * Gemini, and the counts that come back are whatever the model read rather
 * than the numbers we drew. Remove the model and this stops working.
 *
 * The layout mirrors backend/scripts/ward_photo.py, which draws the same board
 * for command-line checks — two renderers, one board, so a discrepancy between
 * them is visible rather than silent.
 */

const WIDTH = 900;
const HEIGHT = 620;

export interface BoardContents {
  beds_total: number;
  beds_occupied: number;
  code: string;
  ward?: string;
}

/**
 * A plausible occupancy for a ward of this size. The model is not told this
 * number — it has to read it off the board — so it is the answer the check is
 * scored against, not an input to the check.
 */
export function plausibleOccupancy(bedsTotal: number): number {
  const share = 0.4 + Math.random() * 0.45;
  return Math.max(1, Math.min(bedsTotal, Math.round(bedsTotal * share)));
}

function label(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, size: number, colour: string) {
  ctx.fillStyle = colour;
  ctx.font = `${size}px "IBM Plex Sans", system-ui, sans-serif`;
  ctx.fillText(text, x, y);
}

/** Draws the board and returns it as base64 JPEG — no data: prefix. */
export function drawWardBoard(contents: BoardContents): string {
  const canvas = document.createElement("canvas");
  canvas.width = WIDTH;
  canvas.height = HEIGHT;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("This browser cannot draw the ward board.");

  // Wall behind the board, so the frame reads as an object in a room rather
  // than a full-bleed graphic.
  ctx.fillStyle = "#787c82";
  ctx.fillRect(0, 0, WIDTH, HEIGHT);
  ctx.fillStyle = "#fafaf8";
  ctx.strokeStyle = "#5a5e64";
  ctx.lineWidth = 6;
  ctx.beginPath();
  ctx.roundRect(40, 40, WIDTH - 80, HEIGHT - 80, 10);
  ctx.fill();
  ctx.stroke();

  ctx.textBaseline = "top";
  label(ctx, "WARD BED STATUS", 80, 80, 46, "#1a202c");
  ctx.fillStyle = "#b4b8be";
  ctx.fillRect(80, 140, WIDTH - 160, 3);

  const rows: [string, string, number, string][] = [
    ["WARD", (contents.ward ?? "general").toUpperCase(), 38, "#253c6c"],
    ["TOTAL BEDS", String(contents.beds_total), 56, "#253c6c"],
    ["OCCUPIED", String(contents.beds_occupied), 56, "#253c6c"],
    ["CODE", contents.code.toUpperCase(), 64, "#961e28"],
  ];
  let y = 170;
  for (const [key, value, size, colour] of rows) {
    label(ctx, key, 80, y + 5, 28, "#5a606c");
    label(ctx, value, 300, y, size, colour);
    y += 85;
  }

  label(ctx, new Date().toISOString().slice(0, 10), 80, HEIGHT - 110, 26, "#787e88");
  label(ctx, "synthetic board, generated for testing", 330, HEIGHT - 106, 22, "#969ca6");

  // Marker speckle, so the model is not reading a perfect render.
  ctx.fillStyle = "#c8cace";
  for (let i = 0; i < 90; i += 1) {
    ctx.fillRect(50 + Math.random() * (WIDTH - 100), 50 + Math.random() * (HEIGHT - 100), 1, 1);
  }

  // JPEG at 0.9: the endpoint caps the upload at 6 MB and a PNG of this board
  // is several times larger for no gain the model can use.
  return canvas.toDataURL("image/jpeg", 0.9).split(",")[1];
}
