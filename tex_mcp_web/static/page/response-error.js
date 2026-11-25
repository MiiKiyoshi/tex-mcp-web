// What a call the page made says when the server refuses it, in the server's own words, which
// shared/comment-api.js reads from the answer.

import { refusalOf } from "../shared/comment-api.js";

export async function responseError(response) {
  return (await refusalOf(response)).error;
}
