using Amazon.BedrockAgentRuntime.Model;
using Amazon.Runtime.Documents;

namespace KbDemo.Api.Tests;

public class BedrockPassageRetrieverTests
{
    [Fact]
    public void The_request_asks_the_knowledge_base_for_five_results_by_managed_search()
    {
        var request = BedrockPassageRetriever.Request("KB00000001", "Hotel limit in Perth?");

        Assert.Equal("KB00000001", request.KnowledgeBaseId);
        Assert.Equal("Hotel limit in Perth?", request.RetrievalQuery.Text);
        Assert.Equal(5, request.RetrievalConfiguration.ManagedSearchConfiguration.NumberOfResults);
    }

    [Fact]
    public void A_passage_is_named_by_the_file_at_the_end_of_its_S3_location()
    {
        var result = new KnowledgeBaseRetrievalResult
        {
            Location = new RetrievalResultLocation
            {
                S3Location = new RetrievalResultS3Location { Uri = "s3://kb-demo-bucket/corpus/travel.md" },
            },
            Score = 0.7772,
            Content = new RetrievalResultContent { Text = "Perth: A$255 a night." },
        };

        Assert.Equal(
            new Passage("travel.md", 0.7772, "Perth: A$255 a night."),
            BedrockPassageRetriever.ToPassage(result));
    }

    [Fact]
    public void Without_a_location_the_source_uri_in_the_metadata_names_the_file()
    {
        var result = new KnowledgeBaseRetrievalResult
        {
            Metadata = new Dictionary<string, Document> { ["_source_uri"] = new("s3://kb-demo-bucket/expenses.md") },
            Score = 0.3297,
            Content = new RetrievalResultContent { Text = "Dinner: A$52." },
        };

        Assert.Equal("expenses.md", BedrockPassageRetriever.ToPassage(result).Source);
    }

    [Fact]
    public void A_result_missing_its_score_and_text_scores_0_with_empty_text()
    {
        var passage = BedrockPassageRetriever.ToPassage(new KnowledgeBaseRetrievalResult());

        Assert.Equal(new Passage("", 0, ""), passage);
    }
}
