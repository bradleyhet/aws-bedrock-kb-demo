# The .NET API as a Lambda container image: ASP.NET Core on a chiseled runtime image (no shell, no
# package manager, non-root), with the Lambda Web Adapter turning Lambda events into plain HTTP.
# Base images are pinned by digest, as the workflow pins actions by commit.

# Build on the build machine's own architecture. The publish is portable IL, so the arm64 image
# runs it as it is and the SDK never runs under emulation.
FROM --platform=$BUILDPLATFORM mcr.microsoft.com/dotnet/sdk:10.0.403-noble@sha256:de4fe90a98845d75910b9769662465c84de1a934665c431279d1382b2bddc02b AS build
WORKDIR /src
COPY global.json Directory.Build.props Directory.Packages.props ./
COPY src/KbDemo.Api/KbDemo.Api.csproj src/KbDemo.Api/packages.lock.json src/KbDemo.Api/
RUN dotnet restore src/KbDemo.Api --locked-mode
COPY src/KbDemo.Api/ src/KbDemo.Api/
RUN dotnet publish src/KbDemo.Api --no-restore --configuration Release --output /app -p:UseAppHost=false

FROM public.ecr.aws/awsguru/aws-lambda-adapter:1.1.0@sha256:17cfd08eff1dfea3f6a9a1e9c65fdac80aa4919b6085e746615530f43f57d2f1 AS adapter

FROM mcr.microsoft.com/dotnet/aspnet:10.0.14-noble-chiseled@sha256:c89a6a61f494f20a5ae302990e814627a8d91b332fd4a8e7c1707c2d4da9555d
COPY --from=adapter /lambda-adapter /opt/extensions/lambda-adapter
WORKDIR /app
COPY --from=build /app .
# The app listens on 8080, the image's default and the adapter's. The adapter waits for this path
# to answer before it passes on the first request.
ENV AWS_LWA_READINESS_CHECK_PATH=/healthz
USER $APP_UID
ENTRYPOINT ["dotnet", "KbDemo.Api.dll"]
