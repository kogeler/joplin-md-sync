# Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT.
# Run only inside the pinned upstream updater image, with networking disabled.
require "json"
require "yaml"
require "dependabot/python"
require "dependabot/config/file"
require "dependabot/job"
require "dependabot/dependency_group_engine"
require "dependabot/python/file_parser"
require "dependabot/python/file_updater"
require "dependabot/python/update_checker/requirements_updater"

def check(condition, message)
  raise message unless condition
end

root, scenario = ARGV
config = YAML.safe_load(File.read("/acceptance/dependabot.yml"))
pip = config.fetch("updates").select { |entry| entry["package-ecosystem"] == "pip" }
check(pip.length == 1, "Python must have a single updater")
pip = pip.fetch(0)
check(pip.fetch("directory") == "/", "All four audiences must share one update directory")
check(pip.fetch("open-pull-requests-limit") == 1, "Only one Python version-update PR is allowed")
check(pip.fetch("rebase-strategy") == "auto", "Dependabot must refresh its own branch")

security = scenario == "security"
# Pass the actual repository rules into the upstream job and group engine.
# There is no local reimplementation of dependency eligibility or grouping.
job = Dependabot::Job.new_update_job(
  job_id: "acceptance",
  job_definition: {
    "job" => {
      "package-manager" => "pip",
      "source" => { "provider" => "github", "repo" => "fixture/acceptance", "directory" => "/" },
      "allowed-updates" => pip.fetch("allow", [{ "dependency-type" => "direct" }]),
      "ignore-conditions" => pip.fetch("ignore", []),
      "dependency-groups" => pip.fetch("groups").map do |name, rules|
        { "name" => name, "rules" => rules.reject { |key, _| key == "applies-to" },
          "applies-to" => rules.fetch("applies-to", "version-updates") }
      end,
      "existing-pull-requests" => [],
      "dependencies" => nil,
      "lockfile-only" => false,
      "requirements-update-strategy" => nil,
      "security-advisories" => %w(fixture-core fixture-leaf).map do |name|
        { "dependency-name" => name, "affected-versions" => ["<2.0"],
          "patched-versions" => [">=2.0"], "unaffected-versions" => [] }
      end,
      "security-updates-only" => security,
      "update-subdependencies" => true,
      "updating-a-pull-request" => false
    }
  }
)

source = Dependabot::Source.new(provider: "github", repo: "fixture/acceptance", directory: "/")
files = Dir.glob(File.join(root, "requirements-*.*")).sort.map do |path|
  Dependabot::DependencyFile.new(name: File.basename(path), content: File.read(path))
end
dependencies = Dependabot::Python::FileParser.new(
  dependency_files: files, source: source, credentials: []
).parse
check(dependencies.any? { |dep| !dep.top_level? }, "Fixture must contain real transitive dependencies")
eligible = dependencies.select { |dep| job.allowed_update?(dep) }
expected = security ? %w(fixture-core fixture-leaf) : dependencies.map(&:name)
check(eligible.map(&:name).sort == expected.sort, "Repository rules exclude dependencies from maintenance")
engine = Dependabot::DependencyGroupEngine.from_job_config(job: job)
engine.assign_to_groups!(dependencies: eligible)
check(engine.dependency_groups.length == 1, "Python updates must form one group")
check(engine.ungrouped_dependencies.empty?, "Dependencies escaped the common update group")
check(engine.dependency_groups.first.dependencies.length == eligible.length, "Group misses dependencies")

def update(files, dependencies, name, allow_unchanged: false)
  current = dependencies.find { |dep| dep.name == name }
  check(!current.nil?, "Missing fixture dependency #{name}")
  requirements = Dependabot::Python::UpdateChecker::RequirementsUpdater.new(
    requirements: current.requirements,
    update_strategy: Dependabot::RequirementsUpdateStrategy::BumpVersions,
    has_lockfile: true, latest_resolvable_version: "2.0"
  ).updated_requirements
  dependency = Dependabot::Dependency.new(
    name: current.name, version: "2.0", requirements: requirements,
    previous_version: current.version, previous_requirements: current.requirements,
    package_manager: "pip"
  )
  Dependabot::Python::FileUpdater.new(
    dependencies: [dependency], dependency_files: files, credentials: []
  ).updated_dependency_files
rescue RuntimeError => error
  # The upstream public updater signals an already current graph this way.
  raise unless allow_unchanged && error.message == "No files have changed!"
  []
end

if scenario == "incompatible"
  begin
    # Updating the shared core must fail on one parent's incompatible requirement.
    update(files, dependencies, "fixture-core")
    raise "Incompatible update was accepted"
  rescue Dependabot::DependencyFileNotResolvable, Dependabot::SharedHelpers::HelperSubprocessFailed => error
    check(error.message.include?("ResolutionImpossible"), "Failure was not a dependency resolution conflict")
    puts "ACCEPTANCE_RESULT=#{JSON.generate(rejected: true)}"
  end
elsif scenario == "direct"
  updates = update(files, dependencies, "fixture-dev")
  updates.each { |file| File.write(File.join(root, file.name), file.content) }
  puts "ACCEPTANCE_RESULT=#{JSON.generate(updated_files: updates.map(&:name).sort)}"
else
  changed = []
  %w(fixture-core fixture-leaf).each do |name|
    updates = update(files, dependencies, name)
    changed.concat(updates.map(&:name))
    updates.each do |file|
      files[files.index { |existing| existing.name == file.name }] = file
    end
  end
  # Regeneration from already updated locks must not create more changes.
  parsed = Dependabot::Python::FileParser.new(
    dependency_files: files, source: source, credentials: []
  ).parse
  repeated = %w(fixture-core fixture-leaf).flat_map do |name|
    update(files, parsed, name, allow_unchanged: true)
  end
  files.each { |file| File.write(File.join(root, file.name), file.content) }
  audiences = %w(dev test package docs).map { |item| "requirements-#{item}.txt" }
  puts "ACCEPTANCE_RESULT=#{JSON.generate(updated_locks: audiences.select { |name| changed.include?(name) }, second_update: repeated.map(&:name))}"
end
