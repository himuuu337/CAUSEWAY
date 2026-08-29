/**
 * Shown only for the brief window before /api/health has answered. A page
 * whose whole pitch is "nothing appears until it's real" should not itself
 * open on a blank screen while it waits for the first response.
 */
export default function Skeleton() {
  return (
    <div className="wrap" aria-busy="true" aria-label="Loading Causeway">
      <div className="skeleton-masthead">
        <div className="sk sk-title" />
        <div className="sk sk-badges" />
      </div>
      <div className="sk sk-button" />
      <div className="sk sk-card" />
      <div className="sk sk-card" />
    </div>
  )
}
