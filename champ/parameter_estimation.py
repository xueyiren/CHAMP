import louvain
from math import log


def iterative_monolayer_resolution_parameter_estimation(G, gamma=1.0, tol=1e-2, max_iter=25, verbose=False):
    """
    Monolayer variant of ALG. 1 from "Relating modularity maximization and stochastic block models in multilayer
    networks." The nested functions here are just used to match the pseudocode in the paper.

    :param G: input graph
    :param gamma: starting gamma value
    :param tol: convergence tolerance
    :param max_iter: maximum number of iterations
    :param verbose: whether or not to print verbose output
    :return: gamma to which the iteration converged and the resulting partition
    """

    if 'weight' not in G.es:
        G.es['weight'] = [1.0] * G.ecount()
    m = sum(G.es['weight'])

    def maximize_modularity(resolution_param):
        # RBConfigurationVertexPartition implements sum (A_ij - gamma (k_ik_j)/(2m)) delta(sigma_i, sigma_j)
        # i.e. "standard" modularity with resolution parameter
        return louvain.find_partition(G, louvain.RBConfigurationVertexPartition, resolution_parameter=resolution_param,
                                      weights='weight')

    def estimate_SBM_parameters(partition):
        community = partition.membership
        m_in = sum(e['weight'] * (community[e.source] == community[e.target]) for e in G.es)
        kappa_r_list = [0] * len(partition)
        for e in G.es:
            kappa_r_list[community[e.source]] += e['weight']
            kappa_r_list[community[e.target]] += e['weight']
        sum_kappa_sqr = sum(x ** 2 for x in kappa_r_list)

        omega_in = (2 * m_in) / (sum_kappa_sqr / (2 * m))
        # guard for div by zero with single community partition
        omega_out = (2 * m - 2 * m_in) / (2 * m - sum_kappa_sqr / (2 * m)) if len(partition) > 1 else 0

        # return estimates for omega_in, omega_out (for multilayer, this would return theta_in, theta_out, p, K)
        return omega_in, omega_out

    def update_gamma(omega_in, omega_out):
        if omega_out == 0:
            return omega_in / log(omega_in)
        return (omega_in - omega_out) / (log(omega_in) - log(omega_out))

    part, last_gamma = None, None
    for iteration in range(max_iter):
        part = maximize_modularity(gamma)
        omega_in, omega_out = estimate_SBM_parameters(part)

        if omega_in == 0 or omega_in == 1 or omega_in == 1:
            raise ValueError("gamma={:0.3f} resulted in degenerate partition".format(gamma))

        last_gamma = gamma
        gamma = update_gamma(omega_in, omega_out)

        if verbose:
            print("Iter {:>2}: {} communities with Q={:0.3f} and "
                  "gamma={:0.3f}->{:0.3f}".format(iteration, len(part), part.q, last_gamma, gamma))

        if abs(gamma - last_gamma) < tol:
            break  # gamma converged
    else:
        if verbose:
            print("Gamma failed to converge within {} iterations. "
                  "Final move of {:0.3f} was not within tolerance {}".format(max_iter, abs(gamma - last_gamma), tol))

    if verbose:
        print("Returned {} communities with Q={:0.3f} and gamma={:0.3f}".format(len(part), part.q, gamma))

    return gamma, part


def iterative_multilayer_resolution_parameter_estimation(G_intralayer, G_interlayer, layer_vec, gamma=1.0, omega=1.0,
                                                         gamma_tol=1e-2, omega_tol=5e-2, omega_max=1000, max_iter=25,
                                                         model='temporal', verbose=False):
    """
    Multilayer variant of ALG. 1 from "Relating modularity maximization and stochastic block models in multilayer
    networks." The nested functions here are just used to match the pseudocode in the paper.

    :param G_intralayer: input graph containing all intra-layer edges
    :param G_interlayer: input graph containing all inter-layer edges
    :param layer_vec: vector of each vertex's layer membership
    :param gamma: starting gamma value
    :param omega: starting omega value
    :param gamma_tol: convergence tolerance for gamma
    :param omega_tol: convergence tolerance for omega
    :param max_iter: maximum number of iterations
    :param omega_max: maximum allowed value for omega
    :param model: network layer topology (temporal, multilevel, multiplex)
    :param verbose: whether or not to print verbose output
    :return: gamma, omega to which the iteration converged and the resulting partition
    """

    # TODO: non-uniform cases
    # model affects SBM parameter estimation and the updating of omega
    if model is 'temporal' or model is 'multilevel':
        def calculate_persistence(community):
            return sum(community[e.source] == community[e.target] for e in G_interlayer.es)

        def update_omega(theta_in, theta_out, p, K):
            # if p is 1, the optimal omega is infinite (here, omega_max)
            return log(1 + p * K / (1 - p)) / (log(theta_in) - log(theta_out)) if p < 1.0 else omega_max
    elif model is 'multiplex':
        # TODO: persistence calculation requires nonlinear root finding
        def update_omega(theta_in, theta_out, p, K):
            # if p is 1, the optimal omega is infinite (here, omega_max)
            return log(1 + p * K / (1 - p)) / (T * (log(theta_in) - log(theta_out))) if p < 1.0 else omega_max

        raise ValueError("Model {} not yet fully implemented".format(model))
    else:
        raise ValueError("Model {} not yet implemented".format(model))

    if 'weight' not in G_intralayer.es:
        G_intralayer.es['weight'] = [1.0] * G_intralayer.ecount()
    if 'weight' not in G_interlayer.es:
        G_interlayer.es['weight'] = [1.0] * G_interlayer.ecount()

    m = sum(G_intralayer.es['weight']) + sum(G_interlayer.es['weight'])
    T = max(layer_vec) + 1  # layer count
    N = G_interlayer.vcount() // T
    G_interlayer.es['weight'] = [omega] * G_interlayer.ecount()

    assert G_interlayer.vcount() == G_intralayer.vcount() and G_interlayer.vcount() % T == 0 \
           and G_intralayer.vcount() % T == 0, "All layers of graph must be of the same size"

    optimiser = louvain.Optimiser()

    m_t = [0] * T
    for e in G_intralayer.es:
        assert layer_vec[e.source] == layer_vec[e.target], \
            "intralayer graph is malformed: edge {}->{} is across layers".format(e.source, e.target)
        m_t[layer_vec[e.source]] += e['weight']

    def maximize_modularity(intralayer_resolution, interlayer_resolution):
        # RBConfigurationVertexPartitionWeightedLayers implements a multilayer version of "standard" modularity (i.e.
        # the Reichardt and Bornholdt's Potts model with configuration null model).
        G_interlayer.es['weight'] = interlayer_resolution
        intralayer_part = \
            louvain.RBConfigurationVertexPartitionWeightedLayers(G_intralayer, layer_vec=layer_vec, weights='weight',
                                                                 resolution_parameter=intralayer_resolution)
        interlayer_part = louvain.CPMVertexPartition(G_interlayer, resolution_parameter=0.0, weights='weight')
        optimiser.optimise_partition_multiplex([intralayer_part, interlayer_part])
        return intralayer_part

    def estimate_SBM_parameters(partition):
        K = len(partition)

        community = partition.membership
        m_t_in = [0] * T
        for e in G_intralayer.es:
            if community[e.source] == community[e.target] and layer_vec[e.source] == layer_vec[e.target]:
                m_t_in[layer_vec[e.source]] += e['weight']
                m_t_in[layer_vec[e.target]] += e['weight']

        kappa_t_r_list = [[0] * K for _ in range(T)]
        for e in G_intralayer.es:
            layer = layer_vec[e.source]
            kappa_t_r_list[layer][community[e.source]] += e['weight']
            kappa_t_r_list[layer][community[e.target]] += e['weight']
        sum_kappa_t_sqr = [sum(x ** 2 for x in kappa_t_r_list[t]) for t in range(T)]

        theta_in = sum(2 * m_t_in[t] for t in range(T)) / sum(sum_kappa_t_sqr[t] / (2 * m_t[t]) for t in range(T))
        # guard for div by zero with single community partition
        theta_out = sum(2 * m - 2 * m_t_in[t] for t in range(T)) / \
                    sum(2 * m_t[t] - sum_kappa_t_sqr[t] / (2 * m_t[t]) for t in range(T)) if len(partition) > 1 else 0

        pers = calculate_persistence(community)
        # guard for div by zero with single community partition
        # (in this case, all community assignments persist across layers)
        p = max((pers / (N * (T - 1)) - 1 / K) / (1 - 1 / K), 0) if K > 1 else 1

        return theta_in, theta_out, p, K

    def update_gamma(theta_in, theta_out):
        if theta_out == 0:
            return theta_in / log(theta_in)
        return (theta_in - theta_out) / (log(theta_in) - log(theta_out))

    part, K, last_gamma, last_omega = (None,) * 4
    for iteration in range(max_iter):
        part = maximize_modularity(gamma, omega)
        theta_in, theta_out, p, K = estimate_SBM_parameters(part)

        if theta_in == 0 or theta_in == 1 or theta_out == 1:
            raise ValueError("gamma={:0.3f}, omega={:0.3f} resulted in degenerate partition".format(gamma, omega))

        last_gamma, last_omega = gamma, omega
        gamma = update_gamma(theta_in, theta_out)
        omega = update_omega(theta_in, theta_out, p, K)

        if verbose:
            print("Iter {:>2}: {} communities with Q={:0.3f}, gamma={:0.3f}->{:0.3f}, and omega={:0.3f}->{:0.3f}"
                  "".format(iteration, K, part.q, last_gamma, gamma, last_omega, omega))

        if abs(gamma - last_gamma) < gamma_tol and abs(omega - last_omega) < omega_tol:
            break  # gamma and omega converged
    else:
        if verbose:
            print("Parameters failed to converge within {} iterations. "
                  "Final move of ({:0.3f}, {:0.3f}) was not within tolerance ({}, {})"
                  "".format(max_iter, abs(gamma - last_gamma), abs(omega - last_omega), gamma_tol, omega_tol))

    if verbose:
        print("Returned {} communities with Q={:0.3f}, gamma={:0.3f}, "
              "and omega={:0.3f}".format(K, part.q, gamma, omega))

    return gamma, omega, part