using Microsoft.AspNetCore.Http.HttpResults;

namespace KbDemo.Api;

/// <summary>POST /ask: a question in, the passages and the answer out.</summary>
internal static class AskEndpoint
{
    public const string RateLimit = "ask";

    public const string NoQuestion =
        "Send a JSON body with a question, like {\"question\": \"How do I reset my password?\"}";

    public static async Task<Results<Ok<Answer>, ProblemHttpResult>> HandleAsync(
        AskRequest? request,
        AnswerService answers,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(request?.Question))
        {
            return TypedResults.Problem(NoQuestion, statusCode: StatusCodes.Status400BadRequest);
        }

        return TypedResults.Ok(await answers.AnswerAsync(request.Question.Trim(), cancellationToken));
    }
}
