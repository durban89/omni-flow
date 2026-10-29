export interface Env {
  APP_KV: KVNamespace;
  ENVIRONMENT: string;
}

type OfferRecord = {
  destination: string;
  status: "active" | "paused";
  disclosure_required?: boolean;
};

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/healthz") {
      return Response.json({ ok: true, environment: env.ENVIRONMENT });
    }

    if (!url.pathname.startsWith("/out/")) {
      return new Response("Not found", { status: 404 });
    }

    const slug = url.pathname.slice("/out/".length);
    const offer = await env.APP_KV.get<OfferRecord>(`offer:${slug}`, "json");

    if (!offer || offer.status !== "active") {
      return new Response("Offer unavailable", { status: 404 });
    }

    return new Response(null, {
      status: 302,
      headers: {
        Location: offer.destination,
        "Cache-Control": "no-store",
        "X-Robots-Tag": "noindex, nofollow",
      },
    });
  },
};
