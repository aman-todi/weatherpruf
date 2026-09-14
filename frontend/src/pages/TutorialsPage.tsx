const TUTORIALS = [
  {
    src: '/tutorials/connect-from-claude.mp4',
    title: 'Connect from Claude',
    blurb:
      'Add Weatherpruf as a custom connector in Claude and sign in, so the assistant can read and update your closet.',
  },
  {
    src: '/tutorials/batch-add-prompt.mp4',
    title: 'Batch-add by prompt',
    blurb: 'Describe several items in one message and let the assistant add them all at once.',
  },
  {
    src: '/tutorials/batch-add-workflow.mp4',
    title: 'Batch-add: the full workflow',
    blurb:
      'Catalogue a stack of clothes end to end — from the prompt to the items landing in your closet.',
  },
  {
    src: '/tutorials/pick-outfit-prompt.mp4',
    title: 'Pick an outfit by prompt',
    blurb: 'Ask what to wear and let it choose from what you own, with the weather in mind.',
  },
  {
    src: '/tutorials/closet-view.mp4',
    title: 'Browse your closet',
    blurb: 'View and correct what you own in the web app — the source of truth the assistant reads from.',
  },
];

export function TutorialsPage() {
  return (
    <div className="page tutorials-page">
      <div className="page__header">
        <div>
          <h1>Quick tutorials</h1>
          <p className="page__subtitle">Short screen recordings of the app in action.</p>
        </div>
      </div>

      {TUTORIALS.map((tutorial, index) => (
        <section className="card" key={tutorial.src}>
          <h2>
            {index + 1}. {tutorial.title}
          </h2>
          <p className="card__lede">{tutorial.blurb}</p>
          <video className="tutorial__video" controls preload="metadata" playsInline>
            <source src={tutorial.src} type="video/mp4" />
            Your browser can’t play this recording.
          </video>
        </section>
      ))}
    </div>
  );
}
