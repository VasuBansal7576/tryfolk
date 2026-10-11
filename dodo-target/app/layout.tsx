import type { ReactNode } from 'react';
import Link from 'next/link';
import './style.css';
export const metadata={title:'Dodo Special Downloads · owned sandbox',description:'One-time artwork downloads in the owned Dodo test-mode target.'};
export default function Layout({children}:{children:ReactNode}) {
  return <html lang="en"><body><header><Link href="/special-downloads">Special Downloads</Link><span>Owned sandbox · test payments only</span></header><main>{children}</main><footer>Adapted from Dodo Payments’ official MIT demo. Artwork files are local demo samples.</footer></body></html>;
}
