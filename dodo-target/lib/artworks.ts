// Titles retained from the official Special Downloads dashboard at the pinned upstream revision.
export const ARTWORKS = [
  'Impressionist Meadow','Baroque Portrait','Abstract Waves','Renaissance Garden',
  'Cubist Cityscape','Surrealist Dream','Expressionist Storm','Pointillist Lake',
  'Gothic Archway','Romantic Sunset','Minimalist Forest','Realist Harbor',
];
// Locally authored vector samples, replacing upstream's external Picsum placeholders.
// These are sample downloads, not generated premium artworks.
export function sampleArtwork(index:number):string {
  const title=ARTWORKS[index];
  const hue=32+index*23;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600" viewBox="0 0 600 600"><title>${title}, owned demo sample</title><rect width="600" height="600" fill="hsl(${hue} 30% 92%)"/><circle cx="420" cy="180" r="80" fill="hsl(${hue} 50% 65%)"/><path d="M0 420 Q150 ${240+index*10} 300 430 T600 380 V600 H0Z" fill="hsl(${hue} 32% 42%)"/><path d="M0 500 Q220 360 450 480 T600 460 V600 H0Z" fill="hsl(${hue} 36% 25%)"/><text x="32" y="48" font-family="sans-serif" font-size="18" fill="#202722">${title}</text><text x="32" y="574" font-family="sans-serif" font-size="14" fill="white">Special Downloads · owned demo sample ${index+1}/12</text></svg>`;
}
