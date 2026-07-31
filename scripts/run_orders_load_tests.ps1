$hostUrl = $env:LOAD_TEST_HOST

if (-not $hostUrl) {
    $hostUrl = "http://127.0.0.1:8000"
}

$users = @(1, 5, 10, 20, 500)

foreach ($userCount in $users) {
    $spawnRate = $userCount

    if ($userCount -eq 500) {
        $spawnRate = 50
    }

    locust `
      -f tests/load_tests/orders_locustfile.py `
      --headless `
      --users $userCount `
      --spawn-rate $userCount `
      --run-time 2m `
      --host $hostUrl `
      --csv "tests/load_tests/results/orders_${userCount}_users"
}

