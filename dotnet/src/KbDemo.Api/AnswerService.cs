using System.Diagnostics;
using Amazon.Runtime;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.Options;

namespace KbDemo.Api;

/// <summary>
/// Retrieve the passages, then ask the model to answer from them alone, citing each by number.
/// The model sits behind IChatClient, so another provider is a registration, not a rewrite.
/// </summary>
internal sealed partial class AnswerService(
    IPassageRetriever retriever,
    IChatClient chat,
    IOptions<KbDemoOptions> options,
    ILogger<AnswerService> logger)
{
    public async Task<Answer> AnswerAsync(string question, CancellationToken cancellationToken)
    {
        var started = Stopwatch.GetTimestamp();
        var passages = await retriever.RetrieveAsync(question, cancellationToken);
        ChatMessage[] messages =
        [
            new(ChatRole.System, Prompt.System),
            new(ChatRole.User, Prompt.ForQuestion(question, passages)),
        ];
        var settings = new ChatOptions
        {
            ModelId = options.Value.ModelId,
            Temperature = 0,
            MaxOutputTokens = 400,
        };
        try
        {
            var response = await chat.GetResponseAsync(messages, settings, cancellationToken);
            LogAnswered(logger, passages.Count, (long)Stopwatch.GetElapsedTime(started).TotalMilliseconds);
            return new Answer(question, passages, response.Text);
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            // Search still works without the model, so show the passages and say why.
            var reason = error is AmazonServiceException service
                ? $"{service.ErrorCode}: {service.Message}"
                : error.Message;
            LogUnavailable(logger, error, reason);
            return new Answer(question, passages, Unavailable: reason);
        }
    }

    [LoggerMessage(Level = LogLevel.Information, Message = "Answered from {PassageCount} passages in {ElapsedMs} ms")]
    private static partial void LogAnswered(ILogger logger, int passageCount, long elapsedMs);

    [LoggerMessage(Level = LogLevel.Warning, Message = "No answer from the model: {Reason}")]
    private static partial void LogUnavailable(ILogger logger, Exception error, string reason);
}
