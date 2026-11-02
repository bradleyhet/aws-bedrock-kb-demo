using Amazon.BedrockAgentRuntime;
using Amazon.BedrockAgentRuntime.Model;
using Microsoft.Extensions.Options;

namespace KbDemo.Api;

/// <summary>Where passages come from: the knowledge base here, a fake in tests.</summary>
internal interface IPassageRetriever
{
    Task<IReadOnlyList<Passage>> RetrieveAsync(string question, CancellationToken cancellationToken);
}

/// <summary>Retrieve from the Bedrock managed knowledge base.</summary>
internal sealed class BedrockPassageRetriever(
    IAmazonBedrockAgentRuntime runtime,
    IOptions<KbDemoOptions> options) : IPassageRetriever
{
    public const int NumberOfResults = 5;

    public async Task<IReadOnlyList<Passage>> RetrieveAsync(
        string question,
        CancellationToken cancellationToken)
    {
        var response = await runtime.RetrieveAsync(
            Request(options.Value.KnowledgeBaseId, question),
            cancellationToken);
        return [.. (response.RetrievalResults ?? []).Select(ToPassage)];
    }

    internal static RetrieveRequest Request(string knowledgeBaseId, string question) => new()
    {
        KnowledgeBaseId = knowledgeBaseId,
        RetrievalQuery = new KnowledgeBaseQuery { Text = question },
        RetrievalConfiguration = new KnowledgeBaseRetrievalConfiguration
        {
            ManagedSearchConfiguration = new ManagedSearchConfiguration
            {
                NumberOfResults = NumberOfResults,
            },
        },
    };

    /// <summary>A result named by the file at the end of its S3 URI, from either place it appears.</summary>
    internal static Passage ToPassage(KnowledgeBaseRetrievalResult result)
    {
        var uri = result.Location?.S3Location?.Uri;
        if (string.IsNullOrEmpty(uri)
            && result.Metadata?.TryGetValue("_source_uri", out var sourceUri) == true
            && sourceUri.IsString())
        {
            uri = sourceUri.AsString();
        }

        uri ??= "";
        return new Passage(
            uri[(uri.LastIndexOf('/') + 1)..],
            result.Score ?? 0,
            result.Content?.Text ?? "");
    }
}
