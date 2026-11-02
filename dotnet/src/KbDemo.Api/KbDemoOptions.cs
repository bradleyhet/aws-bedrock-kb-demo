using System.ComponentModel.DataAnnotations;

namespace KbDemo.Api;

/// <summary>
/// Settings, from the same environment variables the Python Lambda reads. They are checked at
/// start-up, so a missing one stops the service before it takes a request.
/// </summary>
internal sealed class KbDemoOptions
{
    [Required, ConfigurationKeyName("KB_DEMO_KNOWLEDGE_BASE_ID")]
    public string KnowledgeBaseId { get; set; } = "";

    [Required, ConfigurationKeyName("KB_DEMO_MODEL_ID")]
    public string ModelId { get; set; } = "";

    // Lambda sets AWS_REGION; run locally, the project's region stands.
    [Required, ConfigurationKeyName("AWS_REGION")]
    public string Region { get; set; } = "ap-southeast-2";
}
