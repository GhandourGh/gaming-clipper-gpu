"use client";

const POD = { id: "rfbevtshgimov3", name: "gaming-clipper-gpu-test", gpu: "RTX A4000", price: "$0.17/hour", storage: "50 GB at /workspace", status: "Running" };

export function GpuTest() {
  const jupyter = `https://${POD.id}-8888.proxy.runpod.net/lab`;
  return <>
    <header className="page-head"><div><p className="eyebrow">Experimental</p><h1>GPU rental test</h1><p>This test Pod is separate from your normal clipping pipeline. Use it to compare processing speed before moving anything into the full app.</p></div></header>
    <section className="card" style={{ display: "grid", gap: "1rem" }}>
      <div className="section-head"><div><h2>{POD.name}</h2><p>Runpod Pod · {POD.gpu}</p></div><span className="badge warn live">{POD.status}</span></div>
      <div className="live-stats" aria-label="GPU test details"><div><b>GPU cost</b><strong>{POD.price}</strong></div><div><b>Storage</b><strong>{POD.storage}</strong></div><div><b>Pod ID</b><strong>{POD.id}</strong></div></div>
      <div className="notice info"><b>Testing only.</b> GPU billing continues while the Pod is running. When you finish, stop it in the Runpod console. Stopping ends GPU billing and preserves the workspace disk.</div>
      <div className="row"><a className="btn btn-primary" href={`https://console.runpod.io/pods/${POD.id}`} target="_blank" rel="noreferrer">Open Runpod Pod</a><a className="btn btn-ghost" href="https://console.runpod.io/" target="_blank" rel="noreferrer">Open billing</a></div>
    </section>
    <section className="card" style={{ marginTop: "1rem", display: "grid", gap: "1rem" }}>
      <div><p className="eyebrow">Step 1</p><h2>Upload footage to the GPU Pod</h2><p>Use the Pod’s private Jupyter workspace. Upload your MP4 there; it will appear under <code>/workspace</code> on the GPU machine.</p></div>
      <div className="row"><a className="btn btn-primary" href={jupyter} target="_blank" rel="noreferrer">Open GPU upload workspace</a></div>
      <div className="notice info"><b>After uploading:</b> open a Terminal in Jupyter and run the test command from the next step. This upload is separate from the Mac inbox and will not create a normal clipping job.</div>
    </section>
    <section className="card" style={{ marginTop: "1rem" }}>
      <p className="eyebrow">Step 2</p><h2>Run the speed test</h2>
      <p>Once the clipper test runner is installed on the Pod, choose the uploaded file and start it here. The result will show total time, analysis time, and render time so you can compare it with your Mac.</p>
      <button className="btn btn-ghost" disabled title="The GPU runner is not installed on this Pod yet">Run GPU clipping test</button>
      <p className="muted" style={{ marginTop: ".6rem" }}>The Pod is ready, but the clipping runner still needs to be installed before this button can execute a real test.</p>
    </section>
    <section className="card" style={{ marginTop: "1rem" }}><h2>How this test works</h2><p>Upload one video to the Pod, run the clipping pipeline there, and compare the elapsed time with the Mac. This does not change live recording, normal jobs, or exported clips.</p><p className="muted">Automatic timed stopping is not available through the current Runpod Pod API, so the stop action remains explicit.</p></section>
  </>;
}
